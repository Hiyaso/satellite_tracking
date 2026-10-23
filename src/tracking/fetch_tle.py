from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Project layout is <root>/src/tracking/fetch_tle.py and <root>/data/, so resolve the
# data directory relative to this file rather than the current working directory.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php"


def fetch_gp_by_norad_id(norad_id: int, timeout: float = 15.0) -> list[dict[str, object]]:
    """Fetch the latest GP (OMM) element set for a NORAD catalog number from CelesTrak."""
    url = f"{CELESTRAK_GP_URL}?CATNR={norad_id}&FORMAT=json"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.load(response)

    if not data:
        raise ValueError(f"CelesTrak returned no GP data for NORAD ID {norad_id}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch the latest GP (OMM) element set from CelesTrak and save it to data/"
    )
    parser.add_argument(
        "--norad-id",
        type=int,
        default=25544,
        help="NORAD catalog number to fetch (default: 25544, the ISS)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output filename under data/ (default: gp_<norad-id>.json)",
    )
    args = parser.parse_args()

    output_path = DATA_DIR / (args.output or f"gp_{args.norad_id}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        elements = fetch_gp_by_norad_id(args.norad_id)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise SystemExit(f"Failed to fetch GP data for NORAD ID {args.norad_id}: {exc}")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2)

    name = elements[0].get("OBJECT_NAME", "unknown")
    epoch = elements[0].get("EPOCH", "unknown")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{fetched_at}] Fetched {name} (NORAD {args.norad_id}), epoch {epoch} -> {output_path}")


if __name__ == "__main__":
    main()
