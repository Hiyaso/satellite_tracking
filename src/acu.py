from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sgp4 import omm
from skyfield.api import EarthSatellite, load, wgs84

# Project layout is <root>/src/acu.py and <root>/data/, so resolve the data
# directory relative to this file rather than the current working directory.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def calculate_az_el_from_tle(
    line1: str,
    line2: str,
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float = 0.0,
    when: Optional[datetime] = None,
) -> dict[str, float]:
    """Calculate azimuth/elevation for a satellite from a TLE and a ground station."""
    ts = load.timescale()
    satellite = EarthSatellite(line1, line2, "satellite", ts)

    if when is None:
        when = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    t = ts.from_datetime(when)
    station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)

    alt, az, _ = (satellite - station).at(t).altaz()

    return {
        "azimuth_deg": float(az.degrees),
        "elevation_deg": float(alt.degrees),
    }


def calculate_az_el_over_day(
    line1: str,
    line2: str,
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float = 0.0,
    start: Optional[datetime] = None,
    step_seconds: float = 60.0,
) -> list[dict[str, object]]:
    """Calculate azimuth/elevation for a satellite at regular steps across 24 hours from `start`."""
    ts = load.timescale()
    satellite = EarthSatellite(line1, line2, "satellite", ts)

    if start is None:
        start = datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    num_steps = max(1, int(timedelta(days=1) / timedelta(seconds=step_seconds)))
    times = [start + timedelta(seconds=step_seconds * i) for i in range(num_steps)]
    t = ts.from_datetimes(times)

    station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)
    alt, az, _ = (satellite - station).at(t).altaz()

    return [
        {
            "time": times[i],
            "azimuth_deg": float(az.degrees[i]),
            "elevation_deg": float(alt.degrees[i]),
        }
        for i in range(num_steps)
    ]


def load_gp_elements(path: str, fmt: Optional[str] = None) -> list[dict[str, object]]:
    """Load GP (General Perturbations) element sets from an OMM file.

    Supports the JSON, XML, and CSV formats served by CelesTrak and Space-Track.
    The format is taken from the file extension unless `fmt` is given explicitly.
    Returns a list of OMM field dicts suitable for `EarthSatellite.from_omm()`.
    """
    if fmt is None:
        fmt = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    fmt = fmt.lower().lstrip(".")

    if fmt == "json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else [data]
    if fmt == "xml":
        with open(path, encoding="utf-8") as f:
            return list(omm.parse_xml(f))
    if fmt == "csv":
        with open(path, encoding="utf-8") as f:
            return list(omm.parse_csv(f))

    raise ValueError(f"Unsupported GP format: {fmt!r} (use json, xml, or csv)")


def calculate_az_el_from_gp(
    omm_fields: dict[str, object],
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float = 0.0,
    when: Optional[datetime] = None,
) -> dict[str, float]:
    """Calculate azimuth/elevation from a GP (General Perturbations / OMM) element set."""
    ts = load.timescale()
    satellite = EarthSatellite.from_omm(ts, omm_fields)

    if when is None:
        when = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    t = ts.from_datetime(when)
    station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)

    alt, az, _ = (satellite - station).at(t).altaz()

    return {
        "azimuth_deg": float(az.degrees),
        "elevation_deg": float(alt.degrees),
    }


def calculate_az_el_from_gp_over_day(
    omm_fields: dict[str, object],
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float = 0.0,
    start: Optional[datetime] = None,
    step_seconds: float = 60.0,
) -> list[dict[str, object]]:
    """Calculate az/el from a GP element set at regular steps across 24 hours from `start`."""
    ts = load.timescale()
    satellite = EarthSatellite.from_omm(ts, omm_fields)

    if start is None:
        start = datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    num_steps = max(1, int(timedelta(days=1) / timedelta(seconds=step_seconds)))
    times = [start + timedelta(seconds=step_seconds * i) for i in range(num_steps)]
    t = ts.from_datetimes(times)

    station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)
    alt, az, _ = (satellite - station).at(t).altaz()

    return [
        {
            "time": times[i],
            "azimuth_deg": float(az.degrees[i]),
            "elevation_deg": float(alt.degrees[i]),
        }
        for i in range(num_steps)
    ]


def _parse_when(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use ISO 8601 format, for example 2026-07-13T12:00:00Z"
        ) from exc


# Sample ISS TLE, used as a default so the script can run without CLI args.
# Real usage should pass an up-to-date TLE via --line1/--line2.
_SAMPLE_TLE_LINE1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9994"
_SAMPLE_TLE_LINE2 = "2 25544  51.6416 339.6867 0007976  35.0698  50.6355 15.50357350 39984"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate satellite azimuth/elevation from a TLE or GP (General Perturbations) element set"
    )
    parser.add_argument("--line1", default=_SAMPLE_TLE_LINE1, help="TLE line 1 (default: sample ISS TLE)")
    parser.add_argument("--line2", default=_SAMPLE_TLE_LINE2, help="TLE line 2 (default: sample ISS TLE)")
    parser.add_argument("--lat", type=float, default=25.0330, help="Ground station latitude in degrees")
    parser.add_argument("--lon", type=float, default=121.5654, help="Ground station longitude in degrees")
    parser.add_argument("--elev", type=float, default=0.0, help="Ground station elevation in meters")
    parser.add_argument("--when", help="Optional ISO 8601 timestamp, e.g. 2026-07-13T12:00:00Z")
    parser.add_argument(
        "--gp-file",
        help="Path to a GP (General Perturbations / OMM) element set to use instead of --line1/--line2",
    )
    parser.add_argument(
        "--gp-format",
        choices=["json", "xml", "csv"],
        help="Format of --gp-file (default: inferred from the file extension)",
    )
    parser.add_argument(
        "--day",
        action="store_true",
        help="Sweep 24 hours starting at --when (default: now) instead of a single point",
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=60.0,
        help="Step size in seconds between samples when --day is set (default: 60)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help=(
            "Write the result to this file as JSON instead of (or in addition to) printing it. "
            f"Relative paths are placed under {DATA_DIR} (default: {DATA_DIR / 'azel.json'} when "
            "--output is given without a value)"
        ),
        nargs="?",
        const="azel.json",
    )
    args = parser.parse_args()

    output_path: Optional[Path] = None
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = DATA_DIR / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

    when = _parse_when(args.when)

    # Choose the element source: a GP (General Perturbations / OMM) file, or a TLE.
    gp_fields: Optional[dict[str, object]] = None
    if args.gp_file:
        elements = load_gp_elements(args.gp_file, args.gp_format)
        if not elements:
            parser.error(f"No GP element sets found in {args.gp_file}")
        if len(elements) > 1:
            name = elements[0].get("OBJECT_NAME", "the first satellite")
            print(f"# {len(elements)} satellites in {args.gp_file}; using {name}")
        gp_fields = elements[0]

    if args.day:
        if gp_fields is not None:
            rows = calculate_az_el_from_gp_over_day(
                gp_fields,
                args.lat,
                args.lon,
                args.elev,
                when,
                args.step_seconds,
            )
        else:
            rows = calculate_az_el_over_day(
                args.line1,
                args.line2,
                args.lat,
                args.lon,
                args.elev,
                when,
                args.step_seconds,
            )
        for row in rows:
            time_str = row["time"].isoformat(timespec="seconds")
            print(f"{time_str}  az={row['azimuth_deg']:7.2f}  el={row['elevation_deg']:7.2f}")

        if output_path:
            json_rows = [
                {
                    "time": row["time"].isoformat(timespec="seconds"),
                    "azimuth_deg": row["azimuth_deg"],
                    "elevation_deg": row["elevation_deg"],
                }
                for row in rows
            ]
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(json_rows, f, indent=2)
            print(f"# Wrote {len(json_rows)} samples to {output_path}")
    else:
        if gp_fields is not None:
            result = calculate_az_el_from_gp(
                gp_fields,
                args.lat,
                args.lon,
                args.elev,
                when,
            )
        else:
            result = calculate_az_el_from_tle(
                args.line1,
                args.line2,
                args.lat,
                args.lon,
                args.elev,
                when,
            )
        print(result)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            print(f"# Wrote result to {output_path}")


if __name__ == "__main__":
    main()
