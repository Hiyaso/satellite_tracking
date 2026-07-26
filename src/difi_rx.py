"""DIFI / VITA 49.2 receiver.

Listens on a UDP port for a DIFI 1.x signal flow, decodes all three packet
types, checks each one against the rules in the standard (IEEE-ISTO Std
4900-2021), and reports conformance, sequence gaps, timestamp behaviour and
signal statistics.

  * Standard Flow Signal Data packet     -- packet type 0x1, class 0x0000/0x0000
  * Standard Flow Signal Context packet  -- packet type 0x4, class 0x0000/0x0001
  * Version Flow Signal Context packet   -- packet type 0x5, class 0x0001/0x0004

Pair it with `difi_tx.py`:

    python src/difi_rx.py --bind 0.0.0.0:4991
    python src/difi_tx.py --dest 127.0.0.1:4991 --duration 10

Only the standard library is used -- no extra dependencies beyond what
`config/requirements.txt` already installs.
"""

from __future__ import annotations

import argparse
import math
import socket
import struct
import sys
import time
from typing import Optional

# --------------------------------------------------------------------------
# DIFI wire constants (IEEE-ISTO Std 4900-2021)
# --------------------------------------------------------------------------

DIFI_OUI = 0x6A621E

PKT_TYPE_DATA = 0x1
PKT_TYPE_CONTEXT = 0x4
PKT_TYPE_VERSION = 0x5
PKT_TYPE_NAMES = {PKT_TYPE_DATA: "data", PKT_TYPE_CONTEXT: "context", PKT_TYPE_VERSION: "version"}

# (Information Class Code, Packet Class Code) expected for each packet type.
EXPECTED_CLASS = {
    PKT_TYPE_DATA: (0x0000, 0x0000),
    PKT_TYPE_CONTEXT: (0x0000, 0x0001),
    PKT_TYPE_VERSION: (0x0001, 0x0004),
}

TSI_NAMES = {0: "not allowed", 1: "UTC", 2: "GPS", 3: "POSIX"}
TSF_REAL_TIME = 0x2
PICOSECONDS_PER_SECOND = 1_000_000_000_000

VALID_CIF0_CONTEXT = (0xFBB98000, 0x7BB98000)
VALID_CIF0_VERSION = (0x80000002, 0x00000002)
CIF1_VERSION = 0x0000000C
V49_SPEC_VERSION = 0x00000004
REF_POINT_IF = 0x64

PROLOGUE_WORDS = 7
CONTEXT_WORDS = 27
VERSION_WORDS = 11

RADIX_FREQ = 20
RADIX_GAIN = 7

REAL_COMPLEX_NAMES = {0: "real", 1: "complex cartesian", 2: "complex polar", 3: "reserved"}
PACKING_NAMES = {0: "processing efficient", 1: "link efficient"}


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------


def _unfixed(value: int, radix: int) -> float:
    """Convert a fixed-point integer back to a real value."""
    return value / float(1 << radix)


class DifiPacket:
    """A decoded DIFI packet.

    Parsing is deliberately tolerant: anything structurally readable is decoded
    so that `errors` can describe exactly how a non-conforming packet deviates,
    rather than the decode failing outright.
    """

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.errors: list[str] = []
        self.fields: dict[str, object] = {}

        if len(raw) < PROLOGUE_WORDS * 4:
            raise ValueError(f"packet is {len(raw)} bytes, shorter than the {PROLOGUE_WORDS * 4} byte DIFI prologue")
        if len(raw) % 4:
            raise ValueError(f"packet is {len(raw)} bytes, not a whole number of 32-bit words")

        header, self.stream_id, class_hi, class_lo, self.seconds, self.picoseconds = struct.unpack(
            ">IIIIIQ", raw[: PROLOGUE_WORDS * 4]
        )

        self.header = header
        self.pkt_type = (header >> 28) & 0xF
        self.class_id_present = (header >> 27) & 0x1
        self.reserved = (header >> 25) & 0x3
        self.tsm = (header >> 24) & 0x1
        self.tsi = (header >> 22) & 0x3
        self.tsf = (header >> 20) & 0x3
        self.seq_num = (header >> 16) & 0xF
        self.size_words = header & 0xFFFF

        self.padding_bits = (class_hi >> 27) & 0x1F
        self.oui = class_hi & 0xFFFFFF
        self.info_class = (class_lo >> 16) & 0xFFFF
        self.packet_class = class_lo & 0xFFFF

        self.payload = raw[PROLOGUE_WORDS * 4 :]
        self.type_name = PKT_TYPE_NAMES.get(self.pkt_type, f"unknown(0x{self.pkt_type:X})")

        self._validate_common()
        if self.pkt_type == PKT_TYPE_CONTEXT:
            self._decode_context()
        elif self.pkt_type == PKT_TYPE_VERSION:
            self._decode_version()
        elif self.pkt_type == PKT_TYPE_DATA:
            self._validate_data()

    # -- common ------------------------------------------------------------

    def _validate_common(self) -> None:
        if self.pkt_type not in EXPECTED_CLASS:
            self.errors.append(f"packet type 0x{self.pkt_type:X} is not a DIFI packet type (expected 0x1, 0x4 or 0x5)")
        if not self.class_id_present:
            self.errors.append("Class ID present bit must be 1")
        if self.reserved:
            self.errors.append(f"reserved header bits must be 0 (got 0b{self.reserved:02b})")
        if self.tsi == 0:
            self.errors.append("TSI must not be 0")
        if self.tsf != TSF_REAL_TIME:
            self.errors.append(f"TSF must be 2 (real time, picoseconds), got {self.tsf}")
        if self.padding_bits:
            self.errors.append(f"Class ID padding bits must be 0 (got {self.padding_bits})")
        if self.oui != DIFI_OUI:
            self.errors.append(f"OUI is 0x{self.oui:06X}, expected 0x{DIFI_OUI:06X}")
        if self.picoseconds >= PICOSECONDS_PER_SECOND:
            self.errors.append(
                f"fractional timestamp {self.picoseconds} ps is not below 1 second "
                f"({PICOSECONDS_PER_SECOND} ps)"
            )
        if self.size_words * 4 != len(self.raw):
            self.errors.append(
                f"header declares {self.size_words} words ({self.size_words * 4} B) "
                f"but the datagram is {len(self.raw)} B"
            )

        expected = EXPECTED_CLASS.get(self.pkt_type)
        if expected:
            info, pkt = expected
            if self.info_class != info:
                self.errors.append(f"Information Class Code is 0x{self.info_class:04X}, expected 0x{info:04X}")
            if self.packet_class != pkt:
                self.errors.append(f"Packet Class Code is 0x{self.packet_class:04X}, expected 0x{pkt:04X}")

        expected_tsm = 0 if self.pkt_type == PKT_TYPE_DATA else 1
        if self.pkt_type in EXPECTED_CLASS and self.tsm != expected_tsm:
            self.errors.append(f"TSM must be {expected_tsm} for a {self.type_name} packet, got {self.tsm}")

    # -- data --------------------------------------------------------------

    def _validate_data(self) -> None:
        if not self.payload:
            self.errors.append("data packet carries no payload")

    def iq_samples(self, bit_depth: int) -> list[tuple[int, int]]:
        """Decode the payload into (I, Q) integer pairs at the given bit depth."""
        if bit_depth not in (8, 16):
            raise ValueError(f"cannot decode {bit_depth}-bit samples (only 8 and 16 are supported)")
        code = "h" if bit_depth == 16 else "b"
        width = 2 * (bit_depth // 8)
        count = len(self.payload) // width
        flat = struct.unpack(f">{2 * count}{code}", self.payload[: count * width])
        return list(zip(flat[0::2], flat[1::2]))

    # -- context -----------------------------------------------------------

    def _decode_context(self) -> None:
        if self.size_words != CONTEXT_WORDS:
            self.errors.append(f"context packet must be {CONTEXT_WORDS} words, got {self.size_words}")
        if len(self.payload) < (CONTEXT_WORDS - PROLOGUE_WORDS) * 4:
            self.errors.append("context packet is truncated")
            return

        cif0, ref_point, bandwidth, if_freq, rf_freq, if_band_offset = struct.unpack(">IIQqqq", self.payload[:40])
        ref_reserved, ref_level, gain2, gain1 = struct.unpack(">hhhh", self.payload[40:48])
        sample_rate, ts_adjust, ts_calibration, state_event, payload_format = struct.unpack(">QqIIQ", self.payload[48:80])

        fmt_hi = payload_format >> 32
        packing = (fmt_hi >> 31) & 0x1
        real_complex = (fmt_hi >> 29) & 0x3
        item_format = (fmt_hi >> 24) & 0x1F
        sample_repeat = (fmt_hi >> 23) & 0x1
        event_tag = (fmt_hi >> 20) & 0x7
        channel_tag = (fmt_hi >> 16) & 0xF
        fraction_size = (fmt_hi >> 12) & 0xF
        packing_field_size = ((fmt_hi >> 6) & 0x3F) + 1
        data_item_size = (fmt_hi & 0x3F) + 1

        self.fields = {
            "cif0": cif0,
            "reference_point": ref_point,
            "bandwidth_hz": _unfixed(bandwidth, RADIX_FREQ),
            "if_freq_hz": _unfixed(if_freq, RADIX_FREQ),
            "rf_freq_hz": _unfixed(rf_freq, RADIX_FREQ),
            "if_band_offset_hz": _unfixed(if_band_offset, RADIX_FREQ),
            "reference_level_dbm": _unfixed(ref_level, RADIX_GAIN),
            "gain_stage1_db": _unfixed(gain1, RADIX_GAIN),
            "gain_stage2_db": _unfixed(gain2, RADIX_GAIN),
            "sample_rate_sps": _unfixed(sample_rate, RADIX_FREQ),
            "timestamp_adjust_fs": ts_adjust,
            "timestamp_calibration": ts_calibration,
            "state_event": state_event,
            "packing_method": PACKING_NAMES.get(packing, "?"),
            "real_complex_type": REAL_COMPLEX_NAMES.get(real_complex, "?"),
            "data_item_format": "signed fixed point" if item_format == 0 else f"0x{item_format:02X}",
            "item_packing_field_size": packing_field_size,
            "data_item_size": data_item_size,
            "bit_depth": data_item_size,
        }

        if cif0 not in VALID_CIF0_CONTEXT:
            self.errors.append(
                f"CIF0 is 0x{cif0:08X}, expected 0xFBB98000 (changed) or 0x7BB98000 (unchanged)"
            )
        if ref_point != REF_POINT_IF:
            self.errors.append(f"Reference Point ID is 0x{ref_point:X}, expected 0x{REF_POINT_IF:X}")
        if ref_reserved:
            self.errors.append(f"reserved half of the Reference Level word must be 0 (got 0x{ref_reserved:04X})")
        if packing != 1:
            self.errors.append("packing method must be link efficient")
        if real_complex != 1:
            self.errors.append(f"real/complex type must be complex cartesian, got {REAL_COMPLEX_NAMES.get(real_complex)}")
        if item_format != 0:
            self.errors.append(f"data item format must be signed fixed point, got 0x{item_format:02X}")
        if sample_repeat:
            self.errors.append("sample repeat indicator must be 0")
        if event_tag:
            self.errors.append(f"event tag size must be 0, got {event_tag}")
        if channel_tag:
            self.errors.append(f"channel tag size must be 0, got {channel_tag}")
        if fraction_size:
            self.errors.append(f"data item fraction size must be 0, got {fraction_size}")
        if not 4 <= data_item_size <= 16:
            self.errors.append(f"data item size {data_item_size} is outside the 4..16 bit range DIFI allows")
        if packing_field_size != data_item_size:
            self.errors.append(
                f"link-efficient packing requires the item packing field size ({packing_field_size}) "
                f"to equal the data item size ({data_item_size})"
            )
        if not 0.0 <= self.fields["rf_freq_hz"] <= 100e9:
            self.errors.append(f"RF reference frequency {self.fields['rf_freq_hz']:,.0f} Hz is outside 0..100 GHz")
        if not 0.0 <= self.fields["if_freq_hz"] <= 100e9:
            self.errors.append(f"IF reference frequency {self.fields['if_freq_hz']:,.0f} Hz is outside 0..100 GHz")
        if self.fields["sample_rate_sps"] <= 0:
            self.errors.append("sample rate must be positive")

    # -- version -----------------------------------------------------------

    def _decode_version(self) -> None:
        if self.size_words != VERSION_WORDS:
            self.errors.append(f"version packet must be {VERSION_WORDS} words, got {self.size_words}")
        if len(self.payload) < (VERSION_WORDS - PROLOGUE_WORDS) * 4:
            self.errors.append("version packet is truncated")
            return

        cif0, cif1, spec_version, info = struct.unpack(">IIII", self.payload[:16])
        year = ((info >> 25) & 0x7F) + 2000
        day = (info >> 16) & 0x1FF
        revision = (info >> 10) & 0x3F
        pkt_kind = (info >> 6) & 0xF
        icd_version = info & 0x3F

        self.fields = {
            "cif0": cif0,
            "cif1": cif1,
            "v49_spec_version": spec_version,
            "year": year,
            "day_of_year": day,
            "revision": revision,
            "type": pkt_kind,
            "icd_version": icd_version,
        }

        if cif0 not in VALID_CIF0_VERSION:
            self.errors.append(f"CIF0 is 0x{cif0:08X}, expected 0x80000002 or 0x00000002")
        if cif1 != CIF1_VERSION:
            self.errors.append(f"CIF1 is 0x{cif1:08X}, expected 0x{CIF1_VERSION:08X}")
        if spec_version != V49_SPEC_VERSION:
            self.errors.append(f"V49 spec version is 0x{spec_version:08X}, expected 0x{V49_SPEC_VERSION:08X}")
        if revision != 1:
            self.errors.append(f"revision must be 1, got {revision}")
        if pkt_kind != 0:
            self.errors.append(f"type must be 0, got {pkt_kind}")
        if icd_version != 0:
            self.errors.append(f"ICD version must be 0, got {icd_version}")

    # -- helpers -----------------------------------------------------------

    @property
    def timestamp(self) -> float:
        """Timestamp as fractional seconds since the epoch named by TSI."""
        return self.seconds + self.picoseconds / PICOSECONDS_PER_SECOND

    def describe(self) -> str:
        lines = [
            f"{self.type_name} packet  stream 0x{self.stream_id:08X}  seq {self.seq_num:2d}  "
            f"{self.size_words} words  TSI={TSI_NAMES.get(self.tsi)}  "
            f"ts={self.seconds}.{self.picoseconds:012d}"
        ]
        for key, value in self.fields.items():
            if isinstance(value, float):
                lines.append(f"    {key:26s} {value:,.6g}")
            elif isinstance(value, int) and key in ("cif0", "cif1", "state_event", "v49_spec_version"):
                lines.append(f"    {key:26s} 0x{value:08X}")
            else:
                lines.append(f"    {key:26s} {value}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Stream tracking
# --------------------------------------------------------------------------


class StreamStats:
    """Per-packet-type counters and continuity checks for one stream ID."""

    def __init__(self) -> None:
        self.counts = {PKT_TYPE_DATA: 0, PKT_TYPE_CONTEXT: 0, PKT_TYPE_VERSION: 0}
        self.conforming = {PKT_TYPE_DATA: 0, PKT_TYPE_CONTEXT: 0, PKT_TYPE_VERSION: 0}
        self.last_seq: dict[int, Optional[int]] = {
            PKT_TYPE_DATA: None,
            PKT_TYPE_CONTEXT: None,
            PKT_TYPE_VERSION: None,
        }
        self.seq_gaps = {PKT_TYPE_DATA: 0, PKT_TYPE_CONTEXT: 0, PKT_TYPE_VERSION: 0}
        self.lost_estimate = 0
        self.last_timestamp: Optional[float] = None
        self.timestamp_regressions = 0
        self.payload_bytes = 0
        self.total_bytes = 0
        self.samples = 0
        self.error_count = 0
        self.context: Optional[DifiPacket] = None
        self.bit_depth = 16
        self.sum_power = 0.0
        self.peak = 0
        self.first_seen: Optional[float] = None
        self.last_seen: Optional[float] = None

    def update(self, packet: DifiPacket, now: float) -> list[str]:
        """Fold one packet into the running statistics, returning continuity errors."""
        issues: list[str] = []
        pkt_type = packet.pkt_type
        if pkt_type not in self.counts:
            return issues

        self.counts[pkt_type] += 1
        self.total_bytes += len(packet.raw)
        if self.first_seen is None:
            self.first_seen = now
        self.last_seen = now

        # Sequence numbers advance modulo 16, independently per packet type.
        previous = self.last_seq[pkt_type]
        if previous is not None and packet.seq_num != (previous + 1) % 16:
            missing = (packet.seq_num - previous - 1) % 16
            self.seq_gaps[pkt_type] += 1
            if pkt_type == PKT_TYPE_DATA:
                self.lost_estimate += missing
            issues.append(
                f"{PKT_TYPE_NAMES[pkt_type]} sequence jumped {previous} -> {packet.seq_num} "
                f"({missing} packet{'s' if missing != 1 else ''} lost or reordered)"
            )
        self.last_seq[pkt_type] = packet.seq_num

        # Timestamps must not go backwards within a flow.
        ts = packet.timestamp
        if self.last_timestamp is not None and ts < self.last_timestamp:
            self.timestamp_regressions += 1
            issues.append(f"timestamp went backwards by {self.last_timestamp - ts:.9f}s")
        self.last_timestamp = ts

        if pkt_type == PKT_TYPE_CONTEXT and not packet.errors:
            self.context = packet
            depth = packet.fields.get("bit_depth")
            if isinstance(depth, int) and depth in (8, 16):
                self.bit_depth = depth

        if pkt_type == PKT_TYPE_DATA:
            self.payload_bytes += len(packet.payload)
            width = 2 * (self.bit_depth // 8)
            if width:
                self.samples += len(packet.payload) // width

        if not packet.errors and not issues:
            self.conforming[pkt_type] += 1
        self.error_count += len(packet.errors) + len(issues)
        return issues

    def measure_signal(self, packet: DifiPacket) -> None:
        """Accumulate power and peak from a data packet's IQ payload."""
        try:
            samples = packet.iq_samples(self.bit_depth)
        except (ValueError, struct.error):
            return
        for i_val, q_val in samples:
            self.sum_power += float(i_val) * i_val + float(q_val) * q_val
            self.peak = max(self.peak, abs(i_val), abs(q_val))


# --------------------------------------------------------------------------
# Receive loop
# --------------------------------------------------------------------------


def _split_host_port(value: str, default_port: int = 4991) -> tuple[str, int]:
    """Parse `host:port`, `host` or `[v6]:port` into a (host, port) pair."""
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        return host, int(rest.lstrip(":")) if rest.lstrip(":") else default_port
    if value.count(":") == 1:
        host, _, port = value.partition(":")
        return host or "0.0.0.0", int(port)
    return value, default_port


def _format_summary(stream_id: int, stats: StreamStats, quiet: bool) -> str:
    lines = [f"stream 0x{stream_id:08X}"]
    total = sum(stats.counts.values())
    for pkt_type in (PKT_TYPE_DATA, PKT_TYPE_CONTEXT, PKT_TYPE_VERSION):
        count = stats.counts[pkt_type]
        if not count:
            continue
        ok = stats.conforming[pkt_type]
        gaps = stats.seq_gaps[pkt_type]
        line = f"  {PKT_TYPE_NAMES[pkt_type]:8s} {count:>10,}  conforming {ok:>10,}"
        if gaps:
            line += f"  sequence gaps {gaps}"
        lines.append(line)

    if stats.counts[PKT_TYPE_DATA]:
        expected = stats.counts[PKT_TYPE_DATA] + stats.lost_estimate
        loss = stats.lost_estimate / expected * 100 if expected else 0.0
        lines.append(f"  samples  {stats.samples:>10,}   payload {stats.payload_bytes:,} B")
        lines.append(f"  loss     {stats.lost_estimate:>10,} packets ({loss:.3f}% of expected)")

    if stats.timestamp_regressions:
        lines.append(f"  timestamp regressions: {stats.timestamp_regressions}")

    duration = (stats.last_seen - stats.first_seen) if stats.first_seen and stats.last_seen else 0.0
    if duration > 0:
        lines.append(
            f"  received {total:,} packets in {duration:.2f}s "
            f"({stats.total_bytes * 8 / duration / 1e6:,.2f} Mb/s)"
        )

    if stats.samples and stats.sum_power > 0:
        full_scale = (1 << (stats.bit_depth - 1)) - 1
        rms = math.sqrt(stats.sum_power / (2 * stats.samples))
        rms_dbfs = 20 * math.log10(rms / full_scale) if rms > 0 else float("-inf")
        peak_dbfs = 20 * math.log10(stats.peak / full_scale) if stats.peak else float("-inf")
        lines.append(f"  signal   RMS {rms_dbfs:6.2f} dBFS   peak {peak_dbfs:6.2f} dBFS   ({stats.bit_depth}-bit)")

    if stats.context and not quiet:
        ctx = stats.context.fields
        lines.append("  last context:")
        lines.append(f"    RF reference   {ctx['rf_freq_hz']:,.0f} Hz")
        lines.append(f"    IF reference   {ctx['if_freq_hz']:,.0f} Hz")
        lines.append(f"    sample rate    {ctx['sample_rate_sps']:,.0f} Sa/s")
        lines.append(f"    bandwidth      {ctx['bandwidth_hz']:,.0f} Hz")
        lines.append(f"    gain           {ctx['gain_stage1_db']:.2f} / {ctx['gain_stage2_db']:.2f} dB")
        lines.append(f"    reference lvl  {ctx['reference_level_dbm']:.2f} dBm")
        lines.append(f"    format         {ctx['data_item_size']}-bit {ctx['real_complex_type']}, {ctx['packing_method']}")

    lines.append(f"  total spec violations: {stats.error_count}")
    return "\n".join(lines)


def receive(args: argparse.Namespace) -> int:
    host, port = _split_host_port(args.bind)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if args.recv_buffer:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, args.recv_buffer)
    sock.bind((host, port))
    sock.settimeout(0.5)

    print(f"DIFI receiver listening on {host}:{port}")
    actual_buf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    print(f"  receive buffer {actual_buf:,} B")
    if args.duration:
        print(f"  stopping after {args.duration}s")
    if args.stream_id is not None:
        print(f"  filtering on stream ID 0x{args.stream_id:08X}")
    print("  waiting for packets... (Ctrl+C to stop)\n")

    streams: dict[int, StreamStats] = {}
    malformed = 0
    filtered = 0
    errors_shown = 0
    start = time.perf_counter()
    last_report = 0.0
    first_packet = True

    try:
        while True:
            elapsed = time.perf_counter() - start
            if args.duration and elapsed >= args.duration:
                break

            try:
                datagram, sender = sock.recvfrom(args.max_datagram)
            except socket.timeout:
                continue

            now = time.perf_counter()
            try:
                packet = DifiPacket(datagram)
            except ValueError as exc:
                malformed += 1
                if errors_shown < args.max_errors:
                    print(f"  [malformed] from {sender[0]}:{sender[1]}: {exc}")
                    errors_shown += 1
                continue

            if args.stream_id is not None and packet.stream_id != args.stream_id:
                filtered += 1
                continue

            if first_packet:
                print(f"  first packet from {sender[0]}:{sender[1]}\n")
                first_packet = False

            stats = streams.setdefault(packet.stream_id, StreamStats())
            issues = stats.update(packet, now)

            if args.measure_signal and packet.pkt_type == PKT_TYPE_DATA:
                stats.measure_signal(packet)

            # Context and version packets are rare and carry the flow's
            # configuration, so print them unless explicitly silenced.
            if packet.pkt_type in (PKT_TYPE_CONTEXT, PKT_TYPE_VERSION) and not args.quiet:
                print(packet.describe())
                print()
            elif args.verbose:
                print(packet.describe())

            for message in packet.errors + issues:
                if errors_shown < args.max_errors:
                    print(f"  [{packet.type_name} seq {packet.seq_num}] {message}")
                    errors_shown += 1
                    if errors_shown == args.max_errors:
                        print(f"  ... further errors suppressed (--max-errors {args.max_errors})")

            if args.report_interval and elapsed - last_report >= args.report_interval:
                counted = sum(sum(s.counts.values()) for s in streams.values())
                print(f"[{elapsed:7.1f}s] {counted:,} packets across {len(streams)} stream(s)")
                last_report = elapsed
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        sock.close()

    print("\n" + "=" * 68)
    print("DIFI receive summary")
    print("=" * 68)
    if not streams:
        print("no DIFI packets received")
    for stream_id, stats in sorted(streams.items()):
        print(_format_summary(stream_id, stats, args.quiet))
    if malformed:
        print(f"\nmalformed datagrams: {malformed}")
    if filtered:
        print(f"datagrams filtered out by --stream-id: {filtered}")

    total_errors = sum(s.error_count for s in streams.values()) + malformed
    if not streams:
        return 1
    if total_errors:
        print(f"\nFAIL: {total_errors} spec violation(s) across {len(streams)} stream(s)")
        return 2
    print("\nPASS: every packet conformed to DIFI 1.x")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Receive and validate a DIFI / VITA 49.2 signal flow over UDP.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--bind", default="0.0.0.0:4991", help="Local address to listen on as host:port")
    parser.add_argument(
        "--stream-id", type=lambda v: int(v, 0), default=None, help="Only accept this stream ID (default: all)"
    )
    parser.add_argument("--duration", type=float, default=0.0, help="Seconds to listen, 0 for until interrupted")
    parser.add_argument("--max-datagram", type=int, default=65535, help="Largest datagram to accept in bytes")
    parser.add_argument("--recv-buffer", type=int, default=0, help="SO_RCVBUF in bytes, 0 to leave the OS default")
    parser.add_argument(
        "--measure-signal",
        action="store_true",
        help="Decode IQ payloads to report RMS and peak level (costs CPU at high rates)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print a line for every packet, including data packets")
    parser.add_argument("--quiet", action="store_true", help="Do not print context and version packet contents")
    parser.add_argument("--max-errors", type=int, default=40, help="Stop printing individual errors after this many")
    parser.add_argument("--report-interval", type=float, default=5.0, help="Seconds between progress lines, 0 to disable")

    args = parser.parse_args()
    sys.exit(receive(args))


if __name__ == "__main__":
    main()
