# Satellite Tracking

Compute where to point a ground-station antenna at a satellite — as a web console,
from the command line, or as a Python library.

| File | What it is |
|---|---|
| **`src/index.html`** | ACU console web UI — set the antenna parameters in a form, get az/el back as a sky plot, timeline, table, pass list, and a live map + readout |
| **`src/world.js`** | Simplified world coastlines drawn by the live map (Natural Earth 1:110m, public domain) |
| **`src/app.py`** | Flask server behind the console: serves the page and exposes the az/el engine over HTTP |
| **`src/acu.py`** | Az/el engine, and a command-line calculator for a single instant or a 24-hour sweep, with optional JSON export |
| **`src/acu_rt.py`** | Real-time az/el tracker; prints the live position until stopped |
| **`src/fetch_tle.py`** | Downloads the latest GP (OMM) element set from CelesTrak into `data/` |

## Quick start

```bash
# Ubuntu / macOS
python -m venv .venv
source .venv/bin/activate
pip install -r config/requirements.txt

python src/fetch_tle.py     # grab a current ISS element set -> data/gp_25544.json
python src/app.py           # open http://127.0.0.1:8000/
```

```powershell
# Windows
python -m venv .venv
.venv\Scripts\activate
pip install -r config/requirements.txt

python src\fetch_tle.py
python src\app.py
```

All commands below are run from the `satellite_tracking/` project root.

## Project layout

```
satellite_tracking/
├── src/                    # index.html, world.js, app.py, acu.py, acu_rt.py, fetch_tle.py
├── data/                   # element sets from fetch_tle.py, JSON exports from acu.py --output
└── config/
    └── requirements.txt    # Python dependencies
```

## Requirements

- Python 3.9+
- Dependencies in `config/requirements.txt`:

  | Package | Used by | Purpose |
  |---|---|---|
  | `skyfield==1.54` | `src/acu.py`, `src/acu_rt.py` | Orbital mechanics / az-el computation |
  | `sgp4==2.27` | `src/acu.py` | TLE and GP (OMM) propagation, parses JSON/XML/CSV element sets |
  | `flask==3.1.3` | `src/app.py` | Web console server (serves the UI and the API) |
  | `flask-cors==6.0.5` | `src/app.py` | CORS support, for calling the API from a page served elsewhere |

Network access is only needed to pull element sets: `fetch_tle.py`, the console's
*Fetch* button, and the `norad` satellite mode. Everything else — propagation, the
plots, the live map — runs offline.

## Web console — set ACU parameters in a browser, get az/el out

```bash
python src/app.py                              # http://127.0.0.1:8000/
python src/app.py --host 0.0.0.0 --port 9000   # reachable from other machines
python src/app.py --debug                      # Flask auto-reload while editing
```

`app.py` serves `index.html` and `world.js` itself, so there is no separate web
server, no build step, and no CDN — the page works offline.

> The console has no authentication and lets any visitor read files from `data/`
> and trigger CelesTrak fetches, so only use `--host 0.0.0.0` on a trusted network.

### What you can set

| Group | Parameters |
|---|---|
| Satellite | GP/OMM file already in `data/`, a manually pasted TLE, or a NORAD ID fetched live from CelesTrak |
| Ground station | Name, latitude, longitude, elevation (m) |
| Time window | Start (UTC, blank = now), duration in minutes, step in seconds |
| Pedestal | Elevation mask, az/el offsets (bias), azimuth travel limits (cable wrap), elevation limits, max az/el slew rates |

### What you get back

- **Track** — summary tiles (samples, time above mask, max elevation, min range, peak axis rates, limit hits), a sky plot of the pass with the mask ring, and a commanded az/el timeline. Segments where the antenna is not tracking are drawn faintly.
- **Data** — the full sample table: time, true az/el, commanded az/el, axis rates, slant range, and per-sample flags. Download as CSV or JSON.
- **Passes** — AOS / TCA / LOS with times, azimuths, peak elevation, and duration.
- **Live** — a world map, plus tiles for true az/el, commanded az/el, slant range, ACU state, sub-satellite latitude/longitude and altitude, polled once per second.

`Save preset` keeps the current parameters in the browser's local storage so they
come back on the next visit.

### Live map

Opening the **Live** tab plots the satellite's position straight away; press
**Live** to keep it updating once per second. The map shows:

- the sub-satellite point (the spot on Earth directly below the satellite) with its name, latitude, longitude, altitude, and timestamp;
- the ground track — half an orbit behind in dim teal, a full orbit ahead in bright teal;
- the footprint circle: inside it the satellite is above your elevation mask;
- your ground station, and a dashed great-circle line to the satellite while it is above the horizon (green once it is above the mask);
- day/night shading, so you can tell whether the pass is in sunlight.

Coastlines are baked into `src/world.js`, so the map needs no tile server and
works offline. It is deliberately coarse — a position reference, not a chart.
The map is an equirectangular projection, so shapes stretch badly near the poles;
that is normal for a ground-track plot and keeps longitude/latitude readable
straight off the axes.

### Sample flags

| Flag | Meaning |
|---|---|
| `BELOW_MASK` | Target is below the elevation mask; the antenna is not tracking it |
| `AZ_LIMIT` / `EL_LIMIT` | Commanded angle is outside the pedestal's travel range |
| `AZ_RATE` / `EL_RATE` | Required slew rate exceeds the configured maximum (a keyhole, typically near zenith) |

Limits and rates are only evaluated while the target is above the mask, since
that is the only time the antenna is following it.

### How the ACU model treats azimuth

Azimuth is unwrapped into a continuous curve before rates are computed, so a
359° → 1° crossing is not reported as a 358°/step slew. The commanded azimuth
for a pass is then shifted by a single multiple of 360° — the wrap branch that
keeps the most of the pass inside the travel limits — because a real pedestal
picks its wrap before acquisition and holds it for the whole pass. Outside
passes the plain 0-360° bearing is shown unless the travel range forbids it.

### HTTP API

| Endpoint | Body | Returns |
|---|---|---|
| `GET /` | — | The console page |
| `GET /world.js` | — | Coastline data for the live map |
| `GET /api/health` | — | Server time |
| `GET /api/gp-files` | — | GP/OMM element files found in `data/` |
| `POST /api/fetch-tle` | `{ "norad_id" }` | Fetches from CelesTrak, saves to `data/`, returns the element summary |
| `POST /api/track` | `{ "satellite", "station", "window", "acu", "visible_only" }` | Samples, summary, and passes |
| `POST /api/live` | same as `/api/track` (window uses `when`) | One instantaneous sample (including the sub-satellite point and altitude) plus the ground track around that instant |
| `POST /api/az-el` | `{ "line1", "line2", "lat", "lon", "elevation_m", "when" }` | Azimuth/elevation at that instant (kept from the original API) |

Example:

```bash
curl -s -X POST http://127.0.0.1:8000/api/track -H 'Content-Type: application/json' -d '{
  "satellite": {"mode": "gp_file", "gp_file": "gp_25544.json"},
  "station":   {"lat": 25.0330, "lon": 121.5654, "elevation_m": 0},
  "window":    {"start": "2026-07-26T03:40:00Z", "duration_minutes": 30, "step_seconds": 5},
  "acu":       {"min_elevation_deg": 10, "max_az_rate_dps": 5, "max_el_rate_dps": 3},
  "visible_only": true
}'
```

`satellite.mode` is `gp_file` (with `gp_file`, and `gp_index` if the file holds
several satellites), `tle` (with `line1`/`line2`), or `norad` (with `norad_id`).
Every parameter has a default, so a body as small as
`{"satellite": {"mode": "gp_file", "gp_file": "gp_25544.json"}}` works.

In `norad` mode the element set is fetched from CelesTrak and then reused for two
minutes, so polling `/api/live` once a second does not mean one CelesTrak request
per second. `POST /api/fetch-tle` always goes to the network, so the *Fetch* button
still gives you a genuine refresh.

`/api/live` takes the same body and answers with a single sample plus what the map
needs:

```jsonc
{
  "satellite": {"name": "ISS (ZARYA)", ...},
  "station":   {"name": "Taipei", "lat": 25.033, "lon": 121.5654, ...},
  "sample":    {"time": "...", "azimuth_deg": 318.4, "elevation_deg": -32.7,
                "commanded_az_deg": ..., "range_km": 7631.0, "visible": false,
                "flags": ["BELOW_MASK"],
                "sub_lat_deg": 51.28, "sub_lon_deg": 31.78, "altitude_km": 427.0},
  "ground_track": {
    "points": [[51.3, 31.8], ...],  // [lat, lon]; null marks a gap, do not join across it
    "now_index": 40,                // points[40] is the position at "time"
    "step_seconds": 69.7,
    "period_minutes": 92.96
  }
}
```

`ground_track` spans half an orbit before the sample and one orbit after it, and is
`null` if the element set cannot be propagated over that span.

Bad input comes back as HTTP 400 with `{"status": "error", "message": ...}`. A
request is capped at 20000 samples; shorten the duration or increase the step if
you hit it.

## `src/acu.py` — command-line az/el calculator

### Basic usage

Run with no arguments to try a sample ISS TLE against a default ground station (Taipei):

```bash
python src/acu.py
```

### Specify your own satellite and ground station

```bash
python src/acu.py --line1 "1 25544U ..." --line2 "2 25544 ..." --lat 25.0330 --lon 121.5654 --elev 0
```

### Specify a date/time

Use `--when` with an ISO 8601 timestamp (UTC if no offset is given). Defaults to now.

```bash
python src/acu.py --when 2026-08-01T00:00:00Z
```

### Sweep a full day

Add `--day` to compute az/el at regular intervals across 24 hours starting at `--when` (or now). Control the interval with `--step-seconds` (default 60).

```bash
python src/acu.py --day --step-seconds 3600 --when 2026-08-01T00:00:00Z
```

### Export to JSON

Add `--output` / `-o` to write the result to a file instead of just printing it. Works both for a single point and for `--day` sweeps (in which case the file contains the full array of samples).

Relative filenames are written under the project's `data/` folder (created automatically if missing), regardless of which directory you run the command from. Absolute paths are used as-is.

```bash
# Writes to data/azel.json
python src/acu.py --day --step-seconds 60 --output azel.json

# --output with no filename also defaults to data/azel.json
python src/acu.py --day --output

# Writes to data/passes/iss.json
python src/acu.py --output passes/iss.json
```

### Use a GP (OMM) element set instead of a TLE

Pass `--gp-file` pointing to a CelesTrak/Space-Track GP file (JSON, XML, or CSV — inferred from the extension, or set explicitly with `--gp-format`):

```bash
python src/acu.py --gp-file iss.json --day --output azel.json
```

If the file contains multiple satellites, the first one is used.

### All options

| Flag | Description |
|---|---|
| `--line1`, `--line2` | TLE lines (default: sample ISS TLE) |
| `--gp-file` | GP/OMM element file to use instead of `--line1`/`--line2` |
| `--gp-format` | `json`, `xml`, or `csv` (default: inferred from file extension) |
| `--lat`, `--lon`, `--elev` | Ground station latitude/longitude (degrees) and elevation (meters) |
| `--when` | ISO 8601 timestamp for the calculation (default: now, UTC) |
| `--day` | Sweep 24 hours starting at `--when` instead of a single point |
| `--step-seconds` | Interval between samples when `--day` is set (default: 60) |
| `--output`, `-o` | Write the result as JSON. Relative paths go under `data/` (default filename `azel.json` if no path given) |

## `src/acu_rt.py` — real-time az/el tracker

Continuously recalculates and prints the satellite's azimuth/elevation for the current time, refreshing in place on a fixed interval, until you stop it with Ctrl+C (or `--duration` elapses).

### Basic usage

```bash
python src/acu_rt.py
```

### Custom satellite, ground station, and refresh rate

```bash
python src/acu_rt.py --line1 "1 25544U ..." --line2 "2 25544 ..." --lat 25.0330 --lon 121.5654 --interval 1
```

### Stop automatically after a fixed duration

```bash
python src/acu_rt.py --duration 30
```

### Use a GP (OMM) element set instead of a TLE

Same as `acu.py` — pass `--gp-file` (and optionally `--gp-format`):

```bash
python src/acu_rt.py --gp-file iss.json --interval 5
```

### All options

| Flag | Description |
|---|---|
| `--line1`, `--line2` | TLE lines (default: sample ISS TLE) |
| `--gp-file` | GP/OMM element file to use instead of `--line1`/`--line2` |
| `--gp-format` | `json`, `xml`, or `csv` (default: inferred from file extension) |
| `--lat`, `--lon`, `--elev` | Ground station latitude/longitude (degrees) and elevation (meters) |
| `--interval` | Seconds between live updates (default: 1) |
| `--duration` | Stop after this many seconds (default: run until Ctrl+C) |

Note: `acu_rt.py` always tracks the real current time — there's no `--when` or `--output`; use `acu.py` for a specific timestamp, a 24-hour sweep, or JSON export. For a live readout with pedestal limits applied, use the console's **Live** tab.

## `src/fetch_tle.py` — refresh GP/TLE data from CelesTrak

Downloads the latest GP (OMM) element set for a satellite from CelesTrak by NORAD catalog number and saves it as JSON under `data/`, ready to hand to `--gp-file` on `acu.py` or `acu_rt.py`, or to pick from the console's element-file list.

### Basic usage

```bash
# Fetches the ISS (NORAD 25544) by default -> data/gp_25544.json
python src/fetch_tle.py
```

### Fetch a different satellite

```bash
python src/fetch_tle.py --norad-id 33591 --output noaa19.json
```

Then use the result:

```bash
python src/acu.py --gp-file data/gp_25544.json --day --output azel.json
python src/acu_rt.py --gp-file data/gp_25544.json
```

### All options

| Flag | Description |
|---|---|
| `--norad-id` | NORAD catalog number to fetch (default: 25544, the ISS) |
| `--output`, `-o` | Output filename under `data/` (default: `gp_<norad-id>.json`) |

### Keeping it fresh automatically

CelesTrak updates most GP data every few hours, so refreshing twice a day keeps propagation error low without hammering the API. On Windows, schedule it with Task Scheduler:

```powershell
$venvPython  = "C:\path\to\satellite_tracking\.venv\Scripts\python.exe"
$scriptPath  = "C:\path\to\satellite_tracking\src\fetch_tle.py"
$projectRoot = "C:\path\to\satellite_tracking"

$Action   = New-ScheduledTaskAction -Execute $venvPython -Argument "`"$scriptPath`"" -WorkingDirectory $projectRoot
$Trigger1 = New-ScheduledTaskTrigger -Daily -At 00:00
$Trigger2 = New-ScheduledTaskTrigger -Daily -At 12:00

Register-ScheduledTask -TaskName "SatelliteTracking-FetchTLE" `
    -Action $Action -Trigger @($Trigger1, $Trigger2) `
    -Description "Fetch latest ISS TLE (NORAD 25544) from CelesTrak, twice daily" `
    -RunLevel Limited
```

This runs while you're logged in (no stored credentials needed). Check on it with:

```powershell
Start-ScheduledTask -TaskName "SatelliteTracking-FetchTLE"   # run once now
Get-ScheduledTaskInfo -TaskName "SatelliteTracking-FetchTLE" # last run time/result
Unregister-ScheduledTask -TaskName "SatelliteTracking-FetchTLE" -Confirm:$false  # remove
```

On Linux/macOS, use a cron entry instead, e.g. `0 0,12 * * * /path/to/.venv/bin/python /path/to/src/fetch_tle.py`.

## Using `acu.py` as a library

`acu.py` holds the engine that both the CLI and the web console call, so you can
import it directly:

```python
import sys
sys.path.insert(0, "src")

from acu import (
    build_satellite, calculate_az_el_series, calculate_ground_track,
    find_passes, load_gp_elements,
)

satellite = build_satellite(omm_fields=load_gp_elements("data/gp_25544.json")[0])
# ...or from a TLE: build_satellite(line1="1 25544U ...", line2="2 25544 ...")

# 30 minutes of look angles at 10-second steps, starting now
for row in calculate_az_el_series(satellite, 25.0330, 121.5654, 0.0, None, 10.0, 180):
    print(row["time"], row["azimuth_deg"], row["elevation_deg"], row["range_km"])

# Where it is over the Earth right now
now = calculate_ground_track(satellite, None, 60.0, 1)[0]
print(now["lat_deg"], now["lon_deg"], now["altitude_km"])

for p in find_passes(satellite, 25.0330, 121.5654, min_elevation_deg=10.0):
    print("AOS", p["rise"]["time"], "peak el", p["culminate"]["elevation_deg"])
```

| Function | Purpose |
|---|---|
| `build_satellite(line1, line2, omm_fields, name, ts)` | Build a Skyfield satellite from a TLE pair or an OMM element dict |
| `calculate_az_el_series(satellite, lat, lon, elev, start, step_seconds, count)` | `count` samples of `time`, `azimuth_deg`, `elevation_deg`, `range_km` |
| `calculate_ground_track(satellite, start, step_seconds, count)` | `count` sub-satellite points: `time`, `lat_deg`, `lon_deg`, `altitude_km` (no ground station needed) |
| `orbital_period_minutes(satellite)` | Orbital period from the element set's mean motion |
| `find_passes(satellite, lat, lon, elev, start, duration_hours, min_elevation_deg)` | Rise / culminate / set events, each with time, az, el, and range |
| `load_gp_elements(path, fmt)` | Read a CelesTrak/Space-Track OMM file (JSON, XML, or CSV) |

The older helpers — `calculate_az_el_from_tle`, `calculate_az_el_from_gp`,
`calculate_az_el_over_day`, `calculate_az_el_from_gp_over_day` — still work
unchanged and are now thin wrappers over `calculate_az_el_series`.

Note that the pedestal model (offsets, travel limits, slew-rate flags, cable wrap)
lives in `app.py`, not `acu.py`: `acu.py` returns true look angles, and `app.py`
turns them into commanded axis angles.
