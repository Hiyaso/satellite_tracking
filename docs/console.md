# Web console — set ACU parameters in a browser, get az/el out

[← README](../README.md) · **Console** · [CLI](cli.md) · [DIFI](difi.md)

```bash
python src/app.py                              # http://127.0.0.1:8000/
python src/app.py --host 0.0.0.0 --port 9000   # reachable from other machines
python src/app.py --debug                      # Flask auto-reload while editing
```

`app.py` serves `index.html` and `world.js` itself, so there is no separate web
server, no build step, and no CDN — the page works offline.

> The console has no authentication and lets any visitor read files from `data/`
> and trigger CelesTrak fetches, so only use `--host 0.0.0.0` on a trusted network.

## What you can set

| Group | Parameters |
|---|---|
| Satellite | GP/OMM file already in `data/`, a manually pasted TLE, or a NORAD ID fetched live from CelesTrak |
| Ground station | Name, latitude, longitude, elevation (m) |
| Time window | Start (UTC, tracks the UTC clock until edited), duration in minutes, step in seconds |
| Pedestal | Elevation mask, az/el offsets (bias), azimuth travel limits (cable wrap), elevation limits, max az/el slew rates |

**Start** is UTC, and shows it. A `datetime-local` input carries no timezone, so
an empty one would have the browser offer your PC's local clock — misleading next
to a field labelled UTC. It is seeded with the UTC wall clock instead and keeps
ticking until you type in it; while untouched the request is still sent with a
blank start, so the window begins at the *server's* clock rather than the
browser's. Clearing the field hands it back to the clock.

Under the duration and step, the panel counts the **data points** the run will
produce — `floor(duration × 60 / step) + 1`, the same arithmetic the server uses
— so the size of a run is visible before you ask for it. It also shows how much
of the 50000-point limit that is. Go over and the box turns red, says by how
much, and names a step that fits:

```
8,641 data points · limit 50,000 · 17% of it
50,005 data points · limit 50,000 — over by 5; use a step of 10.1 s or more
```

The suggested step is always rounded up, so applying it genuinely lands under the
cap. The limit itself is `MAX_SAMPLES` in `src/app.py`, served to the page via
`/api/health`.

## What you get back

- **Track** — summary tiles (samples, time above mask, max elevation, min range, peak axis rates, limit hits), a sky plot of the pass with the mask ring, and a commanded az/el timeline. Arrowheads along the sky track show the direction of travel, so a pass that doubles back in azimuth is not ambiguous. Segments where the antenna is not tracking are drawn faintly.
- **Data** — the full sample table: time, true az/el, commanded az/el, axis rates, slant range, and per-sample flags. Download as CSV or JSON.
- **Passes** — AOS / TCA / LOS with times, azimuths, peak elevation, and duration. Click a pass to narrow every other view to it (see [Choosing a pass](#choosing-a-pass)).
- **Live** — a world map, plus tiles for true az/el, commanded az/el, slant range, ACU state, sub-satellite latitude/longitude and altitude, polled once per second.

`Save preset` keeps the current parameters in the browser's local storage so they
come back on the next visit.

## Choosing a pass

A generated window usually holds several passes. Clicking one in the **Passes**
tab narrows the rest of the console to it:

- the sky plot and the timeline redraw over just that pass;
- the data table shows only its rows;
- the summary tiles are recomputed for it — max elevation, min range and peak
  axis rates then describe that pass rather than the whole window;
- **Download CSV / JSON** export only that pass, named `azel-pass2-<timestamp>`.
  The JSON keeps the full structure, with `passes` narrowed to the chosen one.

A bar above the summary tiles says which pass is showing, with **Show all** to
clear it; clicking the chosen pass again does the same. This filters the samples
already generated — no second request — so switching between passes is instant
and always agrees with the data on screen. Generating again clears the choice,
since pass 2 of the old window is not pass 2 of the new one.

If a pass is shorter than your sample step, nothing was sampled inside it and the
console says so; reduce **Step (s)** and generate again.

## Live map

Opening the **Live** tab plots the satellite's position straight away; press
**Live** to keep it updating once per second. The map shows:

- the sub-satellite point (the spot on Earth directly below the satellite) with its name, latitude, longitude, altitude, and timestamp;
- the ground track — half an orbit behind in dim teal, then the next **three orbits ahead**, each fainter than the last so it is clear which crossing comes first;
- arrowheads along that track, and one just clear of the satellite marker, showing which way it is flying;
- the footprint circle: inside it the satellite is above your elevation mask;
- your ground station, and a dashed great-circle line to the satellite while it is above the horizon (green once it is above the mask);
- day/night shading, so you can tell whether the pass is in sunlight.

Coastlines are baked into `src/world.js`, so the map needs no tile server and
works offline. It is deliberately coarse — a position reference, not a chart.
The map is an equirectangular projection, so shapes stretch badly near the poles;
that is normal for a ground-track plot and keeps longitude/latitude readable
straight off the axes.

## Sample flags

| Flag | Meaning |
|---|---|
| `BELOW_MASK` | Target is below the elevation mask; the antenna is not tracking it |
| `AZ_LIMIT` / `EL_LIMIT` | Commanded angle is outside the pedestal's travel range |
| `AZ_RATE` / `EL_RATE` | Required slew rate exceeds the configured maximum (a keyhole, typically near zenith) |

Limits and rates are only evaluated while the target is above the mask, since
that is the only time the antenna is following it.

## How the ACU model treats azimuth

Azimuth is unwrapped into a continuous curve before rates are computed, so a
359° → 1° crossing is not reported as a 358°/step slew. The commanded azimuth
for a pass is then shifted by a single multiple of 360° — the wrap branch that
keeps the most of the pass inside the travel limits — because a real pedestal
picks its wrap before acquisition and holds it for the whole pass. Outside
passes the plain 0-360° bearing is shown unless the travel range forbids it.

## HTTP API

| Endpoint | Body | Returns |
|---|---|---|
| `GET /` | — | The console page |
| `GET /world.js` | — | Coastline data for the live map |
| `GET /api/health` | — | Server time |
| `GET /api/gp-files` | — | GP/OMM element files found in `data/` |
| `POST /api/fetch-tle` | `{ "norad_id" }` | Fetches from CelesTrak, saves to `data/`, returns the element summary |
| `GET /api/tle-renew` | — | Automatic renewal schedule and per-satellite status |
| `POST /api/tle-renew` | `{ "enabled", "interval_hours", "satellites" }` (all optional) | Updates the schedule, persists it, returns the new status |
| `POST /api/tle-renew/run` | `{ "norad_id" }` (optional) | Renews now, off-schedule; without an ID, every scheduled satellite |
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

`ground_track` spans half an orbit before the sample and three orbits after it —
281 points at 80 per revolution — and is `null` if the element set cannot be
propagated over that span. Change the span with `TRACK_PAST_REVS` /
`TRACK_FUTURE_REVS` in `src/app.py`; the point count follows from them, so the
track keeps its resolution rather than stretching the same points further. Split
the future part into revolutions with `period_minutes × 60 / step_seconds`, which
is how the console shades each orbit differently.

Bad input comes back as HTTP 400 with `{"status": "error", "message": ...}`. A
request is capped at 50000 samples; shorten the duration or increase the step if
you hit it. `GET /api/health` reports the live figures under `limits`
(`max_samples`, `min_step_seconds`), so a client can size a run without
hard-coding them.

## Automatic element-set renewal

Element sets go stale — CelesTrak republishes GP data a few times a day, and SGP4
accuracy falls off either side of the epoch. The console renews the files in
`data/` on a schedule, twice a day by default, from a background thread started
with the server. The *Automatic renewal* box in the parameter panel turns it on
or off, sets the rate, and lists each satellite with its epoch, last renewal, and
time until the next one; *Renew now* fetches immediately.

The schedule lives in `config/tle_renew.json`, seeded on first run from the
`gp_*.json` files already in `data/`:

```jsonc
{
  "enabled": true,
  "interval_hours": 12,                                   // twice a day
  "satellites": [{"norad_id": 25544, "file": "gp_25544.json"}]
}
```

Notes on how it behaves:

- The **file's modification time is the record** of the last renewal — there is no
  separate state file to drift out of step with what is on disk. A satellite whose
  file is older than the interval is renewed as soon as the server starts, so
  downtime does not leave you on stale elements.
- A failed fetch **leaves the existing file untouched** and retries in 15 minutes
  rather than on every pass of the loop, so an outage at CelesTrak cannot cost you
  the elements you already have. The error shows against that satellite in the UI.
- Files are replaced atomically, so a track request that loads an element set
  mid-renewal reads either the old set or the new one, never a truncated file.
- `interval_hours` is floored at 0.5 h so a typo cannot hammer CelesTrak.

Renewal only runs while the console server is up. To keep elements fresh when it
is not, use the OS-level schedule under
[Keeping it fresh automatically](cli.md#keeping-it-fresh-automatically) instead — the two
are independent, so pick one rather than running both against the same file.

---

[← back to the README](../README.md)
