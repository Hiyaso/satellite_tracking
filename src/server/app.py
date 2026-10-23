"""Flask backend for the ACU web console.

Serves the page in `src/web/` and exposes the az/el engine in
`src/tracking/acu.py` over HTTP so the browser UI can configure ACU parameters and get look-angle data back.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

SERVER_DIR = Path(__file__).resolve().parent
TRACKING_DIR = SERVER_DIR.parent / "tracking"
WEB_DIR = SERVER_DIR.parent / "web"
for _path in (SERVER_DIR, TRACKING_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from acu import (  # noqa: E402  (path setup must happen first)
    DATA_DIR,
    build_satellite,
    calculate_az_el_series,
    calculate_ground_track,
    find_passes,
    load_gp_elements,
    orbital_period_minutes,
)
from fetch_tle import fetch_gp_by_norad_id  # noqa: E402
from tle_renew import TleRenewer  # noqa: E402

app = Flask(__name__, static_folder=None)
CORS(app)

# Keeps the element files in data/ fresh on a schedule (twice a day by default).
# Started from __main__ so the debug reloader's parent process does not run a
# second copy of the thread.
renewer = TleRenewer(DATA_DIR, fetch_gp_by_norad_id)

# Guard rails so a mistyped step/duration in the UI cannot ask for a gigabyte of JSON.
# The console shows this count as you set the window, and reports it back in
# `limits` so the page never has to hard-code the number.
MAX_SAMPLES = 50000
MIN_STEP_SECONDS = 0.1

# Ground track drawn on the live map: half a revolution of history so the recent
# path is visible, three revolutions ahead so the next passes are all in view.
# The point count follows from the span rather than being fixed, so changing the
# number of revolutions keeps the same resolution instead of stretching the same
# points over a longer track and going visibly jagged.
TRACK_PAST_REVS = 0.5
TRACK_FUTURE_REVS = 3.0
TRACK_POINTS_PER_REV = 80
TRACK_POINTS = int(round((TRACK_PAST_REVS + TRACK_FUTURE_REVS) * TRACK_POINTS_PER_REV)) + 1

# CelesTrak lookups are memoised because the live panel polls once a second while
# element sets change a few times a day; the explicit "fetch & save" button stays
# uncached so a manual refresh always hits the network.
GP_CACHE_SECONDS = 120.0
_gp_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}


class RequestError(Exception):
    """A bad request from the UI, reported back as HTTP 400."""


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------


def _number(value: Any, name: str, default: Optional[float] = None) -> float:
    if value is None or value == "":
        if default is None:
            raise RequestError(f"Missing value for {name}")
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise RequestError(f"{name} must be a number, got {value!r}")


def _parse_time(value: Optional[str], name: str = "time") -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise RequestError(f"{name} must be ISO 8601, e.g. 2026-08-01T00:00:00Z")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_data_path(filename: str) -> Path:
    """Resolve a filename inside data/, rejecting traversal outside it."""
    candidate = (DATA_DIR / filename).resolve()
    if not str(candidate).startswith(str(DATA_DIR.resolve())):
        raise RequestError(f"Invalid file path: {filename}")
    if not candidate.is_file():
        raise RequestError(f"No such file in data/: {filename}")
    return candidate


def _satellite_from_request(spec: dict[str, Any]):
    """Build a satellite plus display metadata from the UI's satellite panel."""
    mode = (spec.get("mode") or "tle").lower()

    if mode == "tle":
        line1 = (spec.get("line1") or "").strip()
        line2 = (spec.get("line2") or "").strip()
        if not line1 or not line2:
            raise RequestError("Both TLE line 1 and line 2 are required")
        for number, line in ((1, line1), (2, line2)):
            if not line.startswith(f"{number} ") or len(line) < 69:
                raise RequestError(
                    f"TLE line {number} looks malformed: it must start with '{number} ' "
                    "and be 69 characters long"
                )
        name = (spec.get("name") or "").strip() or "Satellite"
        satellite = build_satellite(line1=line1, line2=line2, name=name)
        meta = {"name": name, "source": "TLE"}

    elif mode in ("gp_file", "gp"):
        filename = (spec.get("gp_file") or "").strip()
        if not filename:
            raise RequestError("Choose a GP/OMM file from data/")
        fields = load_gp_elements(str(_safe_data_path(filename)))
        if not fields:
            raise RequestError(f"No element sets found in {filename}")
        index = int(spec.get("gp_index") or 0)
        if not 0 <= index < len(fields):
            raise RequestError(f"Element index {index} out of range for {filename}")
        satellite = build_satellite(omm_fields=fields[index])
        meta = {"name": str(fields[index].get("OBJECT_NAME", filename)), "source": f"GP file {filename}"}
        meta.update(_omm_meta(fields[index]))

    elif mode == "norad":
        norad_id = int(_number(spec.get("norad_id"), "NORAD ID", 25544))
        fields = _fetch_gp_cached(norad_id)
        satellite = build_satellite(omm_fields=fields[0])
        meta = {"name": str(fields[0].get("OBJECT_NAME", f"NORAD {norad_id}")), "source": "CelesTrak live"}
        meta.update(_omm_meta(fields[0]))

    else:
        raise RequestError(f"Unknown satellite mode: {mode}")

    return satellite, meta


def _fetch_gp_cached(norad_id: int) -> list[dict[str, Any]]:
    """Fetch an element set from CelesTrak, reusing a recent result if there is one."""
    cached = _gp_cache.get(norad_id)
    now = time.monotonic()
    if cached and now - cached[0] < GP_CACHE_SECONDS:
        return cached[1]
    fields = fetch_gp_by_norad_id(norad_id)
    _gp_cache[norad_id] = (now, fields)
    return fields


def _omm_meta(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "norad_id": fields.get("NORAD_CAT_ID"),
        "epoch": fields.get("EPOCH"),
        "mean_motion": fields.get("MEAN_MOTION"),
        "inclination": fields.get("INCLINATION"),
    }


def _station_from_request(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": (spec.get("name") or "Ground Station").strip(),
        "lat": _number(spec.get("lat"), "latitude", 25.0330),
        "lon": _number(spec.get("lon"), "longitude", 121.5654),
        "elevation_m": _number(spec.get("elevation_m"), "station elevation", 0.0),
    }


def _acu_from_request(spec: dict[str, Any]) -> dict[str, Any]:
    acu = {
        "min_elevation_deg": _number(spec.get("min_elevation_deg"), "elevation mask", 10.0),
        "az_offset_deg": _number(spec.get("az_offset_deg"), "azimuth offset", 0.0),
        "el_offset_deg": _number(spec.get("el_offset_deg"), "elevation offset", 0.0),
        "az_min_deg": _number(spec.get("az_min_deg"), "azimuth lower limit", -270.0),
        "az_max_deg": _number(spec.get("az_max_deg"), "azimuth upper limit", 270.0),
        "el_min_deg": _number(spec.get("el_min_deg"), "elevation lower limit", 0.0),
        "el_max_deg": _number(spec.get("el_max_deg"), "elevation upper limit", 90.0),
        "max_az_rate_dps": _number(spec.get("max_az_rate_dps"), "max azimuth rate", 5.0),
        "max_el_rate_dps": _number(spec.get("max_el_rate_dps"), "max elevation rate", 3.0),
    }
    if acu["az_min_deg"] >= acu["az_max_deg"]:
        raise RequestError("Azimuth lower limit must be below the upper limit")
    if acu["el_min_deg"] >= acu["el_max_deg"]:
        raise RequestError("Elevation lower limit must be below the upper limit")
    return acu


# ---------------------------------------------------------------------------
# ACU model: turn true look angles into commanded axis angles + rates
# ---------------------------------------------------------------------------


def _unwrap_degrees(values: list[float]) -> list[float]:
    """Make an angle sequence continuous by removing 360° jumps between samples."""
    if not values:
        return []
    out = [values[0]]
    for value in values[1:]:
        delta = (value - out[-1] + 180.0) % 360.0 - 180.0
        out.append(out[-1] + delta)
    return out


def _park_azimuth(az_deg: float, acu: dict[str, Any]) -> float:
    """Map a single azimuth into the travel range.

    Used outside passes, where the pedestal is not tracking this target. The
    plain 0-360° bearing is kept whenever the range allows it, so the commanded
    value only differs from the look angle when the wrap actually requires it.
    """
    centre = (acu["az_min_deg"] + acu["az_max_deg"]) / 2.0
    base = az_deg % 360.0
    if acu["az_min_deg"] <= base <= acu["az_max_deg"]:
        return base
    in_range = [
        c for c in (base - 360.0, base + 360.0)
        if acu["az_min_deg"] <= c <= acu["az_max_deg"]
    ]
    if not in_range:
        return base  # unreachable either way; flagged by the caller
    return min(in_range, key=lambda c: abs(c - centre))


def _wrap_pass(unwrapped: list[float], acu: dict[str, Any]) -> list[float]:
    """Choose one cable-wrap branch for a whole pass.

    A pedestal picks its wrap before acquisition and holds it through the pass,
    so the entire track is shifted by a single multiple of 360° — the one that
    keeps the most of the pass inside the travel limits. Choosing per sample
    instead would invent a 360° unwrap in the middle of a track.
    """
    if not unwrapped:
        return []
    centre = (acu["az_min_deg"] + acu["az_max_deg"]) / 2.0
    span = max(unwrapped) - min(unwrapped)

    def cost(shift: float) -> tuple[int, float]:
        shifted = [v + shift for v in unwrapped]
        outside = sum(1 for v in shifted if not acu["az_min_deg"] <= v <= acu["az_max_deg"])
        track_centre = (max(shifted) + min(shifted)) / 2.0
        return (outside, abs(track_centre - centre))

    # Start from the branch that puts the track near the middle of the range,
    # then test the neighbouring wraps on either side.
    base_shift = -360.0 * round((sum(unwrapped) / len(unwrapped) - centre) / 360.0)
    shifts = [base_shift + 360.0 * k for k in (-2, -1, 0, 1, 2)]
    best = min(shifts, key=cost)
    return [v + best for v in unwrapped]


def _visible_runs(visibility: list[bool]) -> list[tuple[int, int]]:
    """Index ranges [start, end) of contiguous above-mask samples."""
    runs: list[tuple[int, int]] = []
    start: Optional[int] = None
    for index, visible in enumerate(visibility):
        if visible and start is None:
            start = index
        elif not visible and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(visibility)))
    return runs


def _apply_acu(rows: list[dict[str, Any]], acu: dict[str, Any], step_seconds: float) -> list[dict[str, Any]]:
    """Annotate raw look angles with commanded angles, axis rates, and limit flags."""
    # SGP4 returns NaN rather than raising when an element set cannot be
    # propagated (bad checksum, decayed object, epoch far from the window).
    # NaN is not valid JSON, so catch it here and report a usable error.
    if any(math.isnan(row["azimuth_deg"]) or math.isnan(row["elevation_deg"]) for row in rows):
        raise RequestError(
            "The element set could not be propagated over this window "
            "(check the TLE/GP values and how far the epoch is from the start time)"
        )

    az_true = [float(row["azimuth_deg"]) for row in rows]
    el_true = [float(row["elevation_deg"]) for row in rows]
    visibility = [el >= acu["min_elevation_deg"] for el in el_true]

    # Axis rates come from the continuously unwrapped angle so a 359°->1°
    # crossing does not look like a 358°/step slew.
    unwrapped = _unwrap_degrees([az + acu["az_offset_deg"] for az in az_true])
    cmd_el_all = [el + acu["el_offset_deg"] for el in el_true]

    # Commanded azimuth: one wrap branch per contiguous pass, parked position
    # otherwise (below the mask the antenna is not following this target).
    cmd_az_all = [_park_azimuth(v, acu) for v in unwrapped]
    for start, end in _visible_runs(visibility):
        cmd_az_all[start:end] = _wrap_pass(unwrapped[start:end], acu)

    samples: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        cmd_az, cmd_el = cmd_az_all[index], cmd_el_all[index]
        if index == 0:
            az_rate = el_rate = 0.0
        else:
            az_rate = abs(unwrapped[index] - unwrapped[index - 1]) / step_seconds
            el_rate = abs(cmd_el_all[index] - cmd_el_all[index - 1]) / step_seconds

        visible = visibility[index]
        flags: list[str] = []
        if not visible:
            # Below the mask the antenna is not tracking, so axis limits and
            # rates are not meaningful there — only report the mask itself.
            flags.append("BELOW_MASK")
        else:
            if not acu["az_min_deg"] <= cmd_az <= acu["az_max_deg"]:
                flags.append("AZ_LIMIT")
            if not acu["el_min_deg"] <= cmd_el <= acu["el_max_deg"]:
                flags.append("EL_LIMIT")
            if az_rate > acu["max_az_rate_dps"]:
                flags.append("AZ_RATE")
            if el_rate > acu["max_el_rate_dps"]:
                flags.append("EL_RATE")

        samples.append(
            {
                "time": row["time"].isoformat(timespec="seconds"),
                "azimuth_deg": round(az_true[index], 4),
                "elevation_deg": round(el_true[index], 4),
                "range_km": round(float(row["range_km"]), 3),
                "commanded_az_deg": round(cmd_az, 4),
                "commanded_el_deg": round(cmd_el, 4),
                "az_rate_dps": round(az_rate, 4),
                "el_rate_dps": round(el_rate, 4),
                "visible": visible,
                "flags": flags,
            }
        )

    return samples


def _summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {"samples": 0, "visible_samples": 0, "violations": 0}

    visible = [s for s in samples if s["visible"]]
    tracked_rates = visible or samples
    return {
        "samples": len(samples),
        "visible_samples": len(visible),
        "visible_percent": round(100.0 * len(visible) / len(samples), 1),
        "start": samples[0]["time"],
        "end": samples[-1]["time"],
        "max_elevation_deg": round(max(s["elevation_deg"] for s in samples), 3),
        "min_range_km": round(min(s["range_km"] for s in samples), 3),
        "peak_az_rate_dps": round(max(s["az_rate_dps"] for s in tracked_rates), 4),
        "peak_el_rate_dps": round(max(s["el_rate_dps"] for s in tracked_rates), 4),
        "violations": sum(1 for s in samples if any(f != "BELOW_MASK" for f in s["flags"])),
    }


def _ground_track(satellite, when: datetime) -> Optional[dict[str, Any]]:
    """Sub-satellite path around `when`, for the live map.

    Returns None rather than raising when the element set cannot be propagated
    over the whole span: the map is a nice-to-have next to the look angles, so a
    satellite that only propagates near the current instant should still show its
    marker instead of failing the whole live poll.
    """
    try:
        period_minutes = orbital_period_minutes(satellite)
    except (ValueError, AttributeError):
        return None

    span_minutes = period_minutes * (TRACK_PAST_REVS + TRACK_FUTURE_REVS)
    step_seconds = span_minutes * 60.0 / (TRACK_POINTS - 1)
    start = when - timedelta(minutes=period_minutes * TRACK_PAST_REVS)
    rows = calculate_ground_track(satellite, start, step_seconds, TRACK_POINTS)

    points: list[Optional[list[float]]] = []
    for row in rows:
        lat, lon = float(row["lat_deg"]), float(row["lon_deg"])
        # A break in the path (NaN) is kept as a null so the client does not join
        # the samples either side of it into a straight line across the map.
        points.append(None if math.isnan(lat) or math.isnan(lon) else [round(lat, 3), round(lon, 3)])

    now_index = int(round((TRACK_POINTS - 1) * TRACK_PAST_REVS / (TRACK_PAST_REVS + TRACK_FUTURE_REVS)))
    return {
        "points": points,
        "now_index": now_index,
        "step_seconds": round(step_seconds, 3),
        "period_minutes": round(period_minutes, 3),
    }


def _serialize_pass(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, stamp in entry.items():
        out[key] = {
            "time": stamp["time"].isoformat(timespec="seconds"),
            "azimuth_deg": round(stamp["azimuth_deg"], 2),
            "elevation_deg": round(stamp["elevation_deg"], 2),
            "range_km": round(stamp["range_km"], 1),
        }
    if "rise" in entry and "set" in entry:
        out["duration_seconds"] = int((entry["set"]["time"] - entry["rise"]["time"]).total_seconds())
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.errorhandler(RequestError)
def _handle_request_error(exc: RequestError):
    return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/css/<path:filename>")
@app.route("/js/<path:filename>")
def web_asset(filename: str):
    """Stylesheets and scripts for the page; send_from_directory refuses traversal."""
    folder = request.path.split("/")[1]
    return send_from_directory(WEB_DIR / folder, filename)


@app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "success",
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # So the console can count points against the real limits instead of
            # keeping its own copy of them in step with this file.
            "limits": {"max_samples": MAX_SAMPLES, "min_step_seconds": MIN_STEP_SECONDS},
        }
    )


@app.route("/api/gp-files")
def gp_files():
    """List GP/OMM element files sitting in data/ so the UI can offer them."""
    files = []
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            elements = load_gp_elements(str(path))
        except (ValueError, OSError, UnicodeDecodeError):
            continue
        if not elements or not isinstance(elements[0], dict) or "MEAN_MOTION" not in elements[0]:
            continue  # not an element set (e.g. an azel.json export)
        files.append(
            {
                "file": path.name,
                "count": len(elements),
                "satellites": [
                    {
                        "index": i,
                        "name": str(el.get("OBJECT_NAME", f"#{i}")),
                        "norad_id": el.get("NORAD_CAT_ID"),
                        "epoch": el.get("EPOCH"),
                    }
                    for i, el in enumerate(elements[:50])
                ],
            }
        )
    return jsonify({"status": "success", "files": files})


@app.route("/api/fetch-tle", methods=["POST"])
def fetch_tle():
    """Pull a fresh element set from CelesTrak and save it under data/."""
    body = request.get_json(silent=True) or {}
    norad_id = int(_number(body.get("norad_id"), "NORAD ID", 25544))
    try:
        elements = fetch_gp_by_norad_id(norad_id)
    except Exception as exc:  # network/timeout/empty response
        return jsonify({"status": "error", "message": f"CelesTrak fetch failed: {exc}"}), 502

    filename = f"gp_{norad_id}.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / filename).write_text(json.dumps(elements, indent=2), encoding="utf-8")
    return jsonify(
        {
            "status": "success",
            "file": filename,
            "satellite": _omm_meta(elements[0]) | {"name": elements[0].get("OBJECT_NAME")},
        }
    )


@app.route("/api/tle-renew", methods=["GET", "POST"])
def tle_renew():
    """Read or update the automatic element-set renewal schedule."""
    if request.method == "GET":
        return jsonify({"status": "success"} | renewer.status())

    body = request.get_json(silent=True) or {}
    patch: dict[str, Any] = {}
    if "enabled" in body:
        patch["enabled"] = bool(body["enabled"])
    if body.get("interval_hours") is not None:
        patch["interval_hours"] = _number(body["interval_hours"], "renewal interval")
    if body.get("satellites") is not None:
        patch["satellites"] = body["satellites"]
    return jsonify({"status": "success"} | renewer.configure(patch))


@app.route("/api/tle-renew/run", methods=["POST"])
def tle_renew_run():
    """Renew now, off-schedule. Without a NORAD ID, every configured satellite."""
    body = request.get_json(silent=True) or {}
    norad_id = body.get("norad_id")
    status = renewer.renew_now(int(norad_id) if norad_id is not None else None)
    failed = [s for s in status["satellites"] if s["error"]]
    if failed and len(failed) == len(status["satellites"]):
        message = "; ".join(f"{s['norad_id']}: {s['error']}" for s in failed)
        return jsonify({"status": "error", "message": f"CelesTrak fetch failed: {message}"} | status), 502
    return jsonify({"status": "success"} | status)


@app.route("/api/track", methods=["POST"])
def track():
    """Main endpoint: ACU parameters in, az/el track out."""
    body = request.get_json(silent=True) or {}

    satellite, sat_meta = _satellite_from_request(body.get("satellite") or {})
    station = _station_from_request(body.get("station") or {})
    acu = _acu_from_request(body.get("acu") or {})

    window = body.get("window") or {}
    start = _parse_time(window.get("start"), "start time")
    duration_minutes = _number(window.get("duration_minutes"), "duration", 1440.0)
    step_seconds = _number(window.get("step_seconds"), "step", 60.0)

    if step_seconds < MIN_STEP_SECONDS:
        raise RequestError(f"Step must be at least {MIN_STEP_SECONDS} seconds")
    if duration_minutes <= 0:
        raise RequestError("Duration must be greater than zero")

    count = int(duration_minutes * 60 / step_seconds) + 1
    if count > MAX_SAMPLES:
        raise RequestError(
            f"{count} samples requested; the limit is {MAX_SAMPLES}. "
            "Shorten the duration or increase the step size."
        )

    rows = calculate_az_el_series(
        satellite,
        station["lat"],
        station["lon"],
        station["elevation_m"],
        start,
        step_seconds,
        count,
    )
    samples = _apply_acu(rows, acu, step_seconds)

    passes = [
        _serialize_pass(p)
        for p in find_passes(
            satellite,
            station["lat"],
            station["lon"],
            station["elevation_m"],
            start,
            duration_minutes / 60.0,
            acu["min_elevation_deg"],
        )
    ]

    if body.get("visible_only"):
        samples = [s for s in samples if s["visible"]]

    return jsonify(
        {
            "status": "success",
            "satellite": sat_meta,
            "station": station,
            "acu": acu,
            "window": {
                "start": rows[0]["time"].isoformat(timespec="seconds"),
                "duration_minutes": duration_minutes,
                "step_seconds": step_seconds,
            },
            "summary": _summarize(samples),
            "passes": passes,
            "samples": samples,
        }
    )


@app.route("/api/live", methods=["POST"])
def live():
    """Single-instant look angles, polled by the UI's live tracking panel."""
    body = request.get_json(silent=True) or {}

    satellite, sat_meta = _satellite_from_request(body.get("satellite") or {})
    station = _station_from_request(body.get("station") or {})
    acu = _acu_from_request(body.get("acu") or {})
    when = _parse_time((body.get("window") or {}).get("when"), "time")

    rows = calculate_az_el_series(
        satellite, station["lat"], station["lon"], station["elevation_m"], when, 1.0, 1
    )
    sample = _apply_acu(rows, acu, 1.0)[0]

    # Where the satellite is over the Earth right now, which is what the map plots.
    instant = rows[0]["time"]
    subpoint = calculate_ground_track(satellite, instant, 1.0, 1)[0]
    sample["sub_lat_deg"] = round(float(subpoint["lat_deg"]), 4)
    sample["sub_lon_deg"] = round(float(subpoint["lon_deg"]), 4)
    sample["altitude_km"] = round(float(subpoint["altitude_km"]), 3)

    return jsonify(
        {
            "status": "success",
            "satellite": sat_meta,
            "station": station,
            "sample": sample,
            "ground_track": _ground_track(satellite, instant),
        }
    )


@app.route("/api/az-el", methods=["POST"])
def az_el():
    """Backwards-compatible endpoint kept from the original API."""
    body = request.get_json(silent=True) or {}
    satellite, _ = _satellite_from_request(
        {"mode": "tle", "line1": body.get("line1"), "line2": body.get("line2")}
    )
    row = calculate_az_el_series(
        satellite,
        _number(body.get("lat"), "lat", 25.0330),
        _number(body.get("lon"), "lon", 121.5654),
        _number(body.get("elevation_m"), "elevation_m", 0.0),
        _parse_time(body.get("when")),
        1.0,
        1,
    )[0]
    return jsonify(
        {
            "status": "success",
            "result": {
                "azimuth_deg": row["azimuth_deg"],
                "elevation_deg": row["elevation_deg"],
            },
        }
    )


@app.errorhandler(Exception)
def _handle_unexpected(exc: Exception):
    if isinstance(exc, RequestError):
        return _handle_request_error(exc)
    code = getattr(exc, "code", 500)
    if isinstance(code, int) and code < 500:
        return jsonify({"status": "error", "message": str(exc)}), code
    app.logger.exception("Unhandled error")
    return jsonify({"status": "error", "message": f"{type(exc).__name__}: {exc}"}), 500


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ACU web console server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask auto-reload/debug")
    args = parser.parse_args()

    # Under the debug reloader only the child process should renew, otherwise two
    # threads fetch the same files.
    if not args.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        renewer.start()
        status = renewer.status()
        if status["enabled"] and status["satellites"]:
            ids = ", ".join(str(s["norad_id"]) for s in status["satellites"])
            print(f"TLE auto-renew: every {status['interval_hours']:g} h for {ids}")

    print(f"ACU console: http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=args.debug)
