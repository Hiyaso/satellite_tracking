# Command-line tools

[← README](../README.md) · [Console](console.md) · **CLI** · [DIFI](difi.md)

`acu.py`, `acu_rt.py` and `fetch_tle.py` do the same job as the console without a browser, and the engine behind them is importable.

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

CelesTrak updates most GP data every few hours, so refreshing twice a day keeps propagation error low without hammering the API.

If you run the web console, it already does this for you — see
[Automatic element-set renewal](console.md#automatic-element-set-renewal). Use an OS-level
schedule instead when the elements must stay fresh with the console shut down.
On Windows, schedule it with Task Scheduler:

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

---

[← back to the README](../README.md)
