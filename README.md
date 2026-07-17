# Satellite Tracking

Tools for tracking a satellite's position from a ground station:

- **`src/acu.py`** — command-line tool that computes azimuth/elevation for a satellite from a TLE or GP (OMM) element set, either at a single instant or swept across a 24-hour period, with optional JSON export.
- **`src/app.py`** — Flask API that exposes next-pass predictions and az/el lookups over HTTP.
- **`src/index.html`** — 3D globe visualization (globe.gl / three.js) with a control panel and pass-prediction HUD, driven by the Flask API.

## Project layout

```
satellite_tracking/
├── src/            # acu.py, app.py, index.html
├── data/           # JSON output written by acu.py --output
├── config/         # configuration files
└── requirements.txt
```

All commands below are run from the `satellite_tracking/` project root.

## Requirements

- Python 3.9+
- Dependencies in `requirements.txt`

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
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
