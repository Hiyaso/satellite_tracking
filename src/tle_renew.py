"""Background renewal of element sets for a configured list of satellites.

Element sets go stale: CelesTrak republishes a satellite's GP data a few times a
day, and SGP4 accuracy drops off either side of the epoch. This keeps the files
under `data/` fresh without anyone pressing a button, by default twice a day.

The last successful renewal is not tracked in a state file: the element file's
modification time already records it, which means the schedule survives a
restart and cannot disagree with what is actually on disk.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_PATH = CONFIG_DIR / "tle_renew.json"

DEFAULT_INTERVAL_HOURS = 12.0        # twice a day
MIN_INTERVAL_HOURS = 0.5             # a floor, so a typo cannot hammer CelesTrak
RETRY_MINUTES = 15.0                 # after a failed fetch, before trying again
POLL_SECONDS = 30.0                  # how often the thread re-checks what is due


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: Optional[datetime]) -> Optional[str]:
    return None if moment is None else moment.astimezone(timezone.utc).isoformat(timespec="seconds")


class TleRenewer:
    """Keeps `data/gp_<norad>.json` files fresh on a fixed interval.

    Safe to drive from several threads at once: the Flask request handlers and
    the background loop share one lock, so a manual "renew now" cannot interleave
    with a scheduled fetch and leave a half-written file behind.
    """

    def __init__(
        self,
        data_dir: Path,
        fetch: Callable[[int], list[dict[str, Any]]],
        config_path: Path = CONFIG_PATH,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._fetch = fetch
        self._config_path = Path(config_path)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Transient, so they are deliberately not persisted: a failure should not
        # outlive the process that saw it.
        self._errors: dict[int, str] = {}
        self._retry_after: dict[int, datetime] = {}
        self._config = self._load_config()

    # -- configuration ----------------------------------------------------

    def _load_config(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
            seeded = self._seed_from_data_dir()
            if seeded:
                raw = {"enabled": True, "interval_hours": DEFAULT_INTERVAL_HOURS, "satellites": seeded}
                self._write_config(raw)
        return self._normalise(raw)

    def _seed_from_data_dir(self) -> list[dict[str, Any]]:
        """First run: adopt the element files already sitting in data/."""
        found: list[dict[str, Any]] = []
        for path in sorted(self._data_dir.glob("gp_*.json")):
            try:
                elements = json.loads(path.read_text(encoding="utf-8"))
                norad_id = int(elements[0]["NORAD_CAT_ID"])
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                continue
            found.append({"norad_id": norad_id, "file": path.name})
        return found

    @staticmethod
    def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
        satellites: list[dict[str, Any]] = []
        seen: set[int] = set()
        for entry in raw.get("satellites") or []:
            try:
                norad_id = int(entry["norad_id"] if isinstance(entry, dict) else entry)
            except (KeyError, TypeError, ValueError):
                continue
            if norad_id in seen:
                continue
            seen.add(norad_id)
            name = entry.get("file") if isinstance(entry, dict) else None
            satellites.append({"norad_id": norad_id, "file": name or f"gp_{norad_id}.json"})
        try:
            interval = float(raw.get("interval_hours", DEFAULT_INTERVAL_HOURS))
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL_HOURS
        return {
            "enabled": bool(raw.get("enabled", True)),
            "interval_hours": max(MIN_INTERVAL_HOURS, interval),
            "satellites": satellites,
        }

    def _write_config(self, config: dict[str, Any]) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def configure(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge a partial config from the UI, persist it, and re-check what is due."""
        with self._lock:
            merged = dict(self._config)
            for key in ("enabled", "interval_hours", "satellites"):
                if key in patch and patch[key] is not None:
                    merged[key] = patch[key]
            self._config = self._normalise(merged)
            self._write_config(self._config)
            # A satellite that was just added should not stay blocked by the
            # backoff left over from an earlier failure.
            self._retry_after.clear()
        self._wake.set()
        return self.status()

    # -- schedule ---------------------------------------------------------

    def _path_for(self, entry: dict[str, Any]) -> Path:
        return self._data_dir / entry["file"]

    def _last_renewed(self, entry: dict[str, Any]) -> Optional[datetime]:
        try:
            mtime = self._path_for(entry).stat().st_mtime
        except OSError:
            return None      # never fetched, or the file was deleted
        return datetime.fromtimestamp(mtime, timezone.utc)

    def _due_at(self, entry: dict[str, Any]) -> datetime:
        """When this satellite next wants a fetch; the past means "right now"."""
        last = self._last_renewed(entry)
        # No file yet (just added, or deleted by hand) means fetch at once.
        scheduled = _utc_now() if last is None else last + timedelta(hours=self._config["interval_hours"])
        # Backoff outranks the schedule, including for a satellite that has never
        # been fetched: without this a satellite whose fetch keeps failing would
        # retry on every pass of the loop instead of every RETRY_MINUTES.
        retry = self._retry_after.get(entry["norad_id"])
        return max(scheduled, retry) if retry else scheduled

    def _epoch_of(self, entry: dict[str, Any]) -> Optional[str]:
        try:
            elements = json.loads(self._path_for(entry).read_text(encoding="utf-8"))
            return str(elements[0].get("EPOCH"))
        except (OSError, ValueError, KeyError, IndexError, AttributeError):
            return None

    def status(self) -> dict[str, Any]:
        with self._lock:
            config = self._config
            satellites = []
            for entry in config["satellites"]:
                last = self._last_renewed(entry)
                satellites.append(
                    {
                        "norad_id": entry["norad_id"],
                        "file": entry["file"],
                        "last_renewed": _iso(last),
                        "next_renewal": _iso(self._due_at(entry)) if config["enabled"] else None,
                        "epoch": self._epoch_of(entry),
                        "error": self._errors.get(entry["norad_id"]),
                    }
                )
        return {
            "enabled": config["enabled"],
            "interval_hours": config["interval_hours"],
            "running": bool(self._thread and self._thread.is_alive()),
            "satellites": satellites,
        }

    # -- fetching ---------------------------------------------------------

    def _renew(self, entry: dict[str, Any]) -> None:
        """Fetch one satellite and replace its file. Caller holds the lock."""
        norad_id = entry["norad_id"]
        try:
            elements = self._fetch(norad_id)
        except Exception as exc:                      # network, timeout, empty reply
            self._errors[norad_id] = str(exc)
            self._retry_after[norad_id] = _utc_now() + timedelta(minutes=RETRY_MINUTES)
            return

        path = self._path_for(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename, so a reader loading the file mid
        # renewal sees either the old element set or the new one, never a
        # truncated file.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(elements, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self._errors.pop(norad_id, None)
        self._retry_after.pop(norad_id, None)

    def renew_now(self, norad_id: Optional[int] = None) -> dict[str, Any]:
        """Fetch immediately, ignoring the schedule. `None` renews every satellite."""
        with self._lock:
            targets = [e for e in self._config["satellites"] if norad_id in (None, e["norad_id"])]
            for entry in targets:
                self._retry_after.pop(entry["norad_id"], None)
                self._renew(entry)
        return self.status()

    def _run_due(self) -> None:
        with self._lock:
            if not self._config["enabled"]:
                return
            for entry in list(self._config["satellites"]):
                # Read the clock per entry rather than once up front: a satellite
                # with no file yet is due at "now", and a timestamp sampled before
                # the loop would sit fractionally behind it and skip the fetch.
                if self._due_at(entry) <= _utc_now():
                    self._renew(entry)

    def _loop(self) -> None:
        while True:
            try:
                self._run_due()
            except Exception:                          # never let the thread die
                pass
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def start(self) -> None:
        """Start the background thread; renewals overdue at startup run at once."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="tle-renew", daemon=True)
            self._thread.start()
