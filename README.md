# Satellite Tracking

Compute where to point a ground-station antenna at a satellite — as a web console,
from the command line, or as a Python library. It also carries a DIFI / VITA 49.2
digitised IF stream, for the signal side of the same ground station.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r config/requirements.txt

python src/fetch_tle.py     # grab a current ISS element set -> data/gp_25544.json
python src/app.py           # open http://127.0.0.1:8000/
```

<details>
<summary>Windows</summary>

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r config/requirements.txt

python src\fetch_tle.py
python src\app.py
```
</details>

All commands are run from the `satellite_tracking/` project root.

## Documentation

| Guide | What is in it |
|---|---|
| **[docs/console.md](docs/console.md)** | The web console: parameters, sky plot and timeline, pass selection, live map, sample flags, the HTTP API, and automatic element-set renewal |
| **[docs/cli.md](docs/cli.md)** | `acu.py`, `acu_rt.py` and `fetch_tle.py` on the command line, scheduling TLE refreshes with cron or Task Scheduler, and using `acu.py` as a library |
| **[docs/difi.md](docs/difi.md)** | `difi_tx.py` / `difi_rx.py` — DIFI 1.x (VITA 49.2) transmitter and validating receiver |

> The console has no authentication and lets any visitor read files from `data/`
> and trigger CelesTrak fetches, so only use `--host 0.0.0.0` on a trusted network.

## What is here

| File | What it is |
|---|---|
| **`src/index.html`** | ACU console web UI — set the antenna parameters in a form, get az/el back as a sky plot, timeline, table, pass list, and a live map + readout |
| **`src/world.js`** | Simplified world coastlines drawn by the live map (Natural Earth 1:110m, public domain) |
| **`src/app.py`** | Flask server behind the console: serves the page and exposes the az/el engine over HTTP |
| **`src/acu.py`** | Az/el engine, and a command-line calculator for a single instant or a 24-hour sweep, with optional JSON export |
| **`src/acu_rt.py`** | Real-time az/el tracker; prints the live position until stopped |
| **`src/fetch_tle.py`** | Downloads the latest GP (OMM) element set from CelesTrak into `data/` |
| **`src/tle_renew.py`** | Keeps those element sets fresh on a schedule, twice a day by default, while the console runs |
| **`src/difi_tx.py`** | DIFI / VITA 49.2 transmitter — streams a synthetic digitised IF flow over UDP |
| **`src/difi_rx.py`** | DIFI / VITA 49.2 receiver — decodes that flow and checks it against the standard |

## Project layout

```
satellite_tracking/
├── src/                    # index.html, world.js, app.py, acu.py, acu_rt.py, fetch_tle.py
│                           # tle_renew.py, difi_tx.py, difi_rx.py
├── docs/                   # console.md, cli.md, difi.md
├── data/                   # element sets from fetch_tle.py, JSON exports from acu.py --output
└── config/
    ├── requirements.txt    # Python dependencies
    └── tle_renew.json      # automatic renewal schedule (created on first run)
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
*Fetch* and *Renew now* buttons, the `norad` satellite mode, and automatic
renewal. Everything else — propagation, the plots, the live map — runs offline.
`tle_renew.py` and the two DIFI applications need nothing beyond the standard
library.
