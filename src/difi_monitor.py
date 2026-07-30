"""Background DIFI/VITA 49.2 receiver status for the web console.

Reuses the packet decoding and per-stream accounting from `difi_rx.py` (the
standalone CLI receiver) so conformance rules cannot drift between the two.
Only the run loop differs: the console needs a stoppable background thread
with a lock-protected snapshot instead of a blocking CLI run that prints as
it goes.
"""

from __future__ import annotations

import math
import socket
import struct
import threading
import time
from collections import deque
from typing import Any, Optional

import numpy as np

from difi_rx import DifiPacket, PKT_TYPE_DATA, PKT_TYPE_NAMES, StreamStats

DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_BIND_PORT = 4991
RECV_TIMEOUT_SECONDS = 0.5
MAX_DATAGRAM = 65535

# Waterfall: one FFT frame (row) per FFT_SIZE complex samples, rate-limited so
# a fast flow (megasamples/sec) does not turn into a megabyte/sec of JSON —
# excess samples between frames are simply dropped, same trade-off a live
# display makes over a recorder.
FFT_SIZE = 512
MIN_FRAME_INTERVAL_SECONDS = 0.08     # caps frame production at ~12.5 fps
WATERFALL_HISTORY_FRAMES = 300        # ~24s of history at the cap above
NOISE_FLOOR_DBFS = -140.0
_FFT_WINDOW = np.hanning(FFT_SIZE)


class _WaterfallState:
    """Per-stream IQ buffer and rolling FFT-frame history."""

    def __init__(self) -> None:
        self.buffer: list[complex] = []
        self.frames: deque[dict[str, Any]] = deque(maxlen=WATERFALL_HISTORY_FRAMES)
        self.next_seq = 0
        self.last_frame_time = 0.0


class DifiMonitor:
    """Runs difi_rx's receive/decode loop in a background thread for the console.

    Mirrors `TleRenewer`'s shape: configure() sets the desired bind address,
    start()/stop() drive the socket thread, and status() gives a snapshot the
    UI can poll.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._host = DEFAULT_BIND_HOST
        self._port = DEFAULT_BIND_PORT
        self._streams: dict[int, StreamStats] = {}
        self._waterfall: dict[int, _WaterfallState] = {}
        self._malformed = 0
        self._bind_error: Optional[str] = None

    # -- configuration ------------------------------------------------------

    def configure(self, host: Optional[str], port: Optional[int]) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("Stop the receiver before changing the bind address")
            if host:
                self._host = host
            if port:
                self._port = int(port)

    # -- status ---------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            streams = [self._stream_summary(stream_id, stats) for stream_id, stats in sorted(self._streams.items())]
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "host": self._host,
                "port": self._port,
                "malformed": self._malformed,
                "bind_error": self._bind_error,
                "streams": streams,
            }

    @staticmethod
    def _stream_summary(stream_id: int, stats: StreamStats) -> dict[str, Any]:
        counts = {PKT_TYPE_NAMES[t]: n for t, n in stats.counts.items()}
        conforming = {PKT_TYPE_NAMES[t]: n for t, n in stats.conforming.items()}
        seq_gaps = {PKT_TYPE_NAMES[t]: n for t, n in stats.seq_gaps.items()}

        expected = stats.counts[PKT_TYPE_DATA] + stats.lost_estimate
        loss_percent = (stats.lost_estimate / expected * 100.0) if expected else 0.0
        duration = (stats.last_seen - stats.first_seen) if stats.first_seen and stats.last_seen else 0.0
        throughput_mbps = (stats.total_bytes * 8 / duration / 1e6) if duration > 0 else 0.0

        signal = None
        if stats.samples and stats.sum_power > 0:
            full_scale = (1 << (stats.bit_depth - 1)) - 1
            rms = math.sqrt(stats.sum_power / (2 * stats.samples))
            rms_dbfs = 20 * math.log10(rms / full_scale) if rms > 0 else float("-inf")
            peak_dbfs = 20 * math.log10(stats.peak / full_scale) if stats.peak else float("-inf")
            signal = {"rms_dbfs": round(rms_dbfs, 2), "peak_dbfs": round(peak_dbfs, 2), "bit_depth": stats.bit_depth}

        context = None
        if stats.context:
            fields = stats.context.fields
            context = {
                "rf_freq_hz": fields.get("rf_freq_hz"),
                "if_freq_hz": fields.get("if_freq_hz"),
                "sample_rate_sps": fields.get("sample_rate_sps"),
                "bandwidth_hz": fields.get("bandwidth_hz"),
                "gain_stage1_db": fields.get("gain_stage1_db"),
                "gain_stage2_db": fields.get("gain_stage2_db"),
                "reference_level_dbm": fields.get("reference_level_dbm"),
                "data_item_size": fields.get("data_item_size"),
                "real_complex_type": fields.get("real_complex_type"),
                "packing_method": fields.get("packing_method"),
            }

        return {
            "stream_id": f"0x{stream_id:08X}",
            "total_packets": sum(stats.counts.values()),
            "counts": counts,
            "conforming": conforming,
            "seq_gaps": seq_gaps,
            "lost_estimate": stats.lost_estimate,
            "loss_percent": round(loss_percent, 3),
            "timestamp_regressions": stats.timestamp_regressions,
            "samples": stats.samples,
            "payload_bytes": stats.payload_bytes,
            "throughput_mbps": round(throughput_mbps, 3),
            "error_count": stats.error_count,
            "signal": signal,
            "context": context,
        }

    # -- waterfall ------------------------------------------------------------

    def waterfall(self, stream_id: int, since_seq: int) -> dict[str, Any]:
        """New FFT frames for one stream since `since_seq`, oldest first."""
        with self._lock:
            wf = self._waterfall.get(stream_id)
            if not wf:
                return {"fft_size": FFT_SIZE, "latest_seq": since_seq, "frames": []}
            frames = [f for f in wf.frames if f["seq"] > since_seq]
            latest = wf.frames[-1]["seq"] if wf.frames else since_seq
            return {"fft_size": FFT_SIZE, "latest_seq": latest, "frames": frames}

    def _feed_waterfall(self, stream_id: int, packet: DifiPacket, bit_depth: int, now: float) -> None:
        try:
            samples = packet.iq_samples(bit_depth)
        except (ValueError, struct.error):
            return
        if not samples:
            return

        full_scale = float((1 << (bit_depth - 1)) - 1)
        with self._lock:
            wf = self._waterfall.setdefault(stream_id, _WaterfallState())
            wf.buffer.extend(complex(i, q) for i, q in samples)
            while len(wf.buffer) >= FFT_SIZE:
                chunk, wf.buffer = wf.buffer[:FFT_SIZE], wf.buffer[FFT_SIZE:]
                if now - wf.last_frame_time < MIN_FRAME_INTERVAL_SECONDS:
                    continue  # drop this chunk; only cadence-limited frames are computed
                wf.last_frame_time = now
                wf.next_seq += 1
                wf.frames.append(self._compute_frame(chunk, full_scale, wf.next_seq))

    @staticmethod
    def _compute_frame(chunk: list[complex], full_scale: float, seq: int) -> dict[str, Any]:
        samples = np.asarray(chunk, dtype=np.complex128) / full_scale
        spectrum = np.fft.fftshift(np.fft.fft(samples * _FFT_WINDOW))
        magnitude_dbfs = 20.0 * np.log10(np.maximum(np.abs(spectrum) / len(samples), 1e-12))
        bins = np.clip(magnitude_dbfs, NOISE_FLOOR_DBFS, 0.0)
        return {"seq": seq, "bins": [round(float(v), 1) for v in bins]}

    # -- run loop -------------------------------------------------------------

    def _run(self, sock: socket.socket) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    datagram, _sender = sock.recvfrom(MAX_DATAGRAM)
                except socket.timeout:
                    continue
                except OSError:
                    break

                now = time.monotonic()
                try:
                    packet = DifiPacket(datagram)
                except ValueError:
                    with self._lock:
                        self._malformed += 1
                    continue

                with self._lock:
                    stats = self._streams.setdefault(packet.stream_id, StreamStats())
                    stats.update(packet, now)
                    bit_depth = stats.bit_depth
                    if packet.pkt_type == PKT_TYPE_DATA:
                        stats.measure_signal(packet)

                if packet.pkt_type == PKT_TYPE_DATA:
                    self._feed_waterfall(packet.stream_id, packet, bit_depth, now)
        finally:
            sock.close()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._bind_error = None
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self._host, self._port))
            except OSError as exc:
                sock.close()
                self._bind_error = str(exc)
                raise RuntimeError(f"Could not bind {self._host}:{self._port}: {exc}") from exc
            sock.settimeout(RECV_TIMEOUT_SECONDS)

            self._streams = {}
            self._waterfall = {}
            self._malformed = 0
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, args=(sock,), name="difi-monitor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread:
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None
