from __future__ import annotations

import argparse
import json
import time
from typing import Optional

from sgp4 import omm
from skyfield.api import EarthSatellite, load, wgs84
from skyfield.toposlib import GeographicPosition


def calculate_az_el_now(satellite: EarthSatellite, station: GeographicPosition, ts) -> dict[str, object]:
    """Calculate azimuth/elevation for a satellite at the current instant."""
    t = ts.now()
    alt, az, _ = (satellite - station).at(t).altaz()

    return {
        "time": t.utc_datetime(),
        "azimuth_deg": float(az.degrees),
        "elevation_deg": float(alt.degrees),
    }


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


def track_realtime(
    satellite: EarthSatellite,
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float = 0.0,
    interval_seconds: float = 1.0,
    duration_seconds: Optional[float] = None,
) -> None:
    """Print live azimuth/elevation, refreshing every `interval_seconds`.

    Runs until Ctrl+C, or until `duration_seconds` elapses if given.
    """
    ts = load.timescale()
    station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)

    start = time.monotonic()
    try:
        while duration_seconds is None or (time.monotonic() - start) < duration_seconds:
            tick = time.monotonic()

            result = calculate_az_el_now(satellite, station, ts)
            time_str = result["time"].strftime("%Y-%m-%dT%H:%M:%SZ")
            print(
                f"\r{time_str}  az={result['azimuth_deg']:7.2f}  el={result['elevation_deg']:7.2f}",
                end="",
                flush=True,
            )

            # Account for how long the calculation itself took so ticks stay on cadence.
            elapsed = time.monotonic() - tick
            time.sleep(max(0.0, interval_seconds - elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopped.")


# Sample ISS TLE, used as a default so the script can run without CLI args.
# Real usage should pass an up-to-date TLE via --line1/--line2.
_SAMPLE_TLE_LINE1 = "1 37606U 11022B   26201.84964429 -.00000211  00000-0  00000-0 0  9990"
_SAMPLE_TLE_LINE2 = "2 37606   0.0080   7.8156 0001539 129.3280 195.3606  1.00267838 55614"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Track satellite azimuth/elevation in real time from a TLE or GP (OMM) element set"
    )
    parser.add_argument("--line1", default=_SAMPLE_TLE_LINE1, help="TLE line 1 (default: sample ISS TLE)")
    parser.add_argument("--line2", default=_SAMPLE_TLE_LINE2, help="TLE line 2 (default: sample ISS TLE)")
    parser.add_argument("--lat", type=float, default=25.0330, help="Ground station latitude in degrees")
    parser.add_argument("--lon", type=float, default=121.5654, help="Ground station longitude in degrees")
    parser.add_argument("--elev", type=float, default=0.0, help="Ground station elevation in meters")
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
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between live updates (default: 1)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Stop after this many seconds (default: run until Ctrl+C)",
    )
    args = parser.parse_args()

    ts = load.timescale()

    if args.gp_file:
        elements = load_gp_elements(args.gp_file, args.gp_format)
        if not elements:
            parser.error(f"No GP element sets found in {args.gp_file}")
        if len(elements) > 1:
            name = elements[0].get("OBJECT_NAME", "the first satellite")
            print(f"# {len(elements)} satellites in {args.gp_file}; using {name}")
        satellite = EarthSatellite.from_omm(ts, elements[0])
    else:
        satellite = EarthSatellite(args.line1, args.line2, "satellite", ts)

    track_realtime(
        satellite,
        args.lat,
        args.lon,
        args.elev,
        args.interval,
        args.duration,
    )


if __name__ == "__main__":
    main()
