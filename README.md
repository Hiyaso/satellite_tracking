# Satellite Tracking

Tools for tracking a satellite's position from a ground station:

- **`src/acu.py`** — command-line tool that computes azimuth/elevation for a satellite from a TLE or GP (OMM) element set, either at a single instant or swept across a 24-hour period, with optional JSON export.
- **`src/acu_rt.py`** — real-time az/el tracker; continuously prints the satellite's live position until stopped.
- **`src/fetch_tle.py`** — downloads the latest GP (OMM) element set for a satellite from CelesTrak and saves it to `data/`.
- **`src/app.py`** — Flask API that exposes next-pass predictions and az/el lookups over HTTP.
- **`src/index.html`** — 3D globe visualization (globe.gl / three.js) with a control panel and pass-prediction HUD, driven by the Flask API.

## Project layout

```
satellite_tracking/
├── src/                    # acu.py, acu_rt.py, fetch_tle.py, app.py, index.html
├── data/                   # JSON output written by acu.py --output and fetch_tle.py
└── config/
    └── requirements.txt    # Python dependencies
```

All commands below are run from the `satellite_tracking/` project root.

## Requirements

- Python 3.9+
- Dependencies in `config/requirements.txt`:

  | Package | Used by | Purpose |
  |---|---|---|
  | `skyfield==1.54` | `src/acu.py` | Orbital mechanics / az-el computation |
  | `sgp4==2.27` | `src/acu.py` | TLE and GP (OMM) propagation, parses JSON/XML/CSV element sets |
  | `flask==3.1.3` | `src/app.py` | Web API server |
  | `flask-cors==6.0.5` | `src/app.py` | CORS support so `index.html` can call the API from a browser |

For Windows Python Environment Setup
```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r config/requirements.txt
```

For Ubuntu Python Environment Setup
```bash
python -m venv .venv
source .venv\bin\activate
pip install -r config/requirements.txt
```

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

Note: `acu_rt.py` always tracks the real current time — there's no `--when` or `--output`; use `acu.py` for a specific timestamp, a 24-hour sweep, or JSON export.

## `src/fetch_tle.py` — refresh GP/TLE data from CelesTrak

Downloads the latest GP (OMM) element set for a satellite from CelesTrak by NORAD catalog number and saves it as JSON under `data/`, ready to hand to `--gp-file` on `acu.py` or `acu_rt.py`.

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

## `src/app.py` — API server

Starts a Flask server on port 8000:

```bash
python src/app.py
```

Endpoints:

- `POST /api/next-pass` — body `{ "name", "line1", "line2" }`, returns predicted rise/culminate/set passes over the next 3 days for the station hardcoded in `app.py` (Taipei by default).
- `POST /api/az-el` — body `{ "line1", "line2", "lat", "lon", "elevation_m", "when" }`, returns azimuth/elevation at that instant.

## `src/index.html` — 3D globe viewer

Open `src/index.html` in a browser. It loads three.js, globe.gl, lil-gui, and satellite.js from public CDNs, so it needs internet access but no build step.

Before use, edit the placeholder backend URL in `index.html` (search for `ociBackendUrl`) to point at wherever you're running `app.py`, e.g. `http://localhost:8000/api/next-pass`.
