"""DIFI / VITA 49.2 transmitter.

Streams a digitised IF/RF flow over UDP using the DIFI 1.x packet set
(IEEE-ISTO Std 4900-2021), which is a constrained profile of VITA 49.2:

  * Standard Flow Signal Data packet     -- packet type 0x1, class 0x0000/0x0000
  * Standard Flow Signal Context packet  -- packet type 0x4, class 0x0000/0x0001
  * Version Flow Signal Context packet   -- packet type 0x5, class 0x0001/0x0004

The IQ payload is synthesised locally (tone, chirp, noise or zeros), so the
transmitter runs standalone with no radio attached. Pair it with `difi_rx.py`
on the receiving end.

Everything on the wire is big-endian. All words are 32 bits.

    python src/difi_tx.py --dest 127.0.0.1:4991 --duration 10

Only the standard library is used -- no extra dependencies beyond what
`config/requirements.txt` already installs.
"""

from __future__ import annotations

import argparse
import math
import random
import socket
import struct
import sys
import time
from typing import Optional

# --------------------------------------------------------------------------
# DIFI wire constants (IEEE-ISTO Std 4900-2021)
# --------------------------------------------------------------------------

DIFI_OUI = 0x6A621E  # Organizationally Unique Identifier, in every Class ID

# Packet types, from the top 4 bits of the header word.
PKT_TYPE_DATA = 0x1
PKT_TYPE_CONTEXT = 0x4
PKT_TYPE_VERSION = 0x5

# Class ID word 2: (Information Class Code << 16) | Packet Class Code.
CLASS_DATA = (0x0000 << 16) | 0x0000
CLASS_CONTEXT = (0x0000 << 16) | 0x0001
CLASS_VERSION = (0x0001 << 16) | 0x0004

# TSI -- Timestamp Integer type. 0 is not allowed by DIFI.
TSI_UTC, TSI_GPS, TSI_POSIX = 1, 2, 3
TSI_NAMES = {"utc": TSI_UTC, "gps": TSI_GPS, "posix": TSI_POSIX}

# TSF -- Timestamp Fractional type. DIFI fixes this at 0x2, real time in
# picoseconds, so the fractional field counts 0 .. 999_999_999_999 ps.
TSF_REAL_TIME = 0x2
PICOSECONDS_PER_SECOND = 1_000_000_000_000

# Context Indicator Field 0 for the standard context packet. Bit 31 is the
# "context changed" flag; clear it to send 0x7BB98000 for an unchanged context.
CIF0_CONTEXT_CHANGED = 0xFBB98000
CIF0_CONTEXT_UNCHANGED = 0x7BB98000

# Version packet indicator fields, fixed by the standard.
CIF0_VERSION_CHANGED = 0x80000002
CIF0_VERSION_UNCHANGED = 0x00000002
CIF1_VERSION = 0x0000000C
V49_SPEC_VERSION = 0x00000004

REF_POINT_IF = 0x64  # 100 -- the DIFI standard reference point

# Fixed sizes in 32-bit words.
PROLOGUE_WORDS = 7  # header, stream ID, 2x class ID, integer + 64-bit fractional time
CONTEXT_WORDS = 27
VERSION_WORDS = 11

# Radix points: these fields are two's-complement fixed point.
RADIX_FREQ = 20  # bandwidth, IF/RF reference frequency, band offset, sample rate
RADIX_GAIN = 7  # gain / attenuation and reference level

# Largest UDP payload that clears a 1500-byte Ethernet MTU without fragmenting
# (1500 - 20 byte IPv4 header - 8 byte UDP header).
MTU_SAFE_UDP_BYTES = 1472


# --------------------------------------------------------------------------
# Field encoders
# --------------------------------------------------------------------------


def header_word(pkt_type: int, tsm: int, tsi: int, seq_num: int, size_words: int) -> int:
    """Build the 32-bit VITA 49.2 header word.

    Layout, most significant bit first: packet type (4), Class ID present (1),
    reserved (2), TSM (1), TSI (2), TSF (2), sequence number (4), size (16).
    DIFI always sets Class ID present, and always uses TSF = real time.
    """
    return (
        (pkt_type & 0xF) << 28
        | 1 << 27  # Class ID present -- mandatory in DIFI
        | (tsm & 0x1) << 24  # 0 for data packets, 1 for context/version
        | (tsi & 0x3) << 22
        | TSF_REAL_TIME << 20
        | (seq_num & 0xF) << 16
        | (size_words & 0xFFFF)
    )


def payload_format_word(bit_depth: int) -> int:
    """Build the 64-bit Data Packet Payload Format field.

    DIFI permits link-efficient packing of complex cartesian samples in signed
    fixed point only, with no event or channel tags, so every field is fixed
    except the two size fields, which both carry `bit_depth - 1`.
    """
    upper = (
        1 << 31  # packing method: link efficient
        | 0b01 << 29  # real/complex type: complex cartesian
        # data item format (bits 28-24) = 0: signed fixed point
        # sample repeat, event tag size, channel tag size, fraction size all 0
        | ((bit_depth - 1) & 0x3F) << 6  # item packing field size
        | ((bit_depth - 1) & 0x3F)  # data item size
    )
    return upper << 32  # repeat count and vector size occupy the low word, both 0


def state_event_word(
    calibrated_time: Optional[bool] = False,
    valid_data: Optional[bool] = True,
    reference_lock: Optional[bool] = False,
    agc_mgc: Optional[bool] = None,
    detected_signal: Optional[bool] = None,
    spectral_inversion: Optional[bool] = None,
    over_range: Optional[bool] = None,
    sample_loss: Optional[bool] = None,
) -> int:
    """Build the State and Event Indicators word.

    Each of the eight indicators has an enable bit in 31..24 and a matching
    indicator bit in 19..12. The enable bit is what makes the indicator
    meaningful, so the two are distinct states rather than one flag:

        None  -- not reported: both bits clear
        False -- reported as false: enable set, indicator clear
        True  -- reported as true: both bits set

    The defaults suit a software source with no disciplined clock: the data is
    valid, and we explicitly report that the time is not calibrated and the
    reference is not locked rather than staying silent about them.
    """
    flags = (
        calibrated_time,
        valid_data,
        reference_lock,
        agc_mgc,
        detected_signal,
        spectral_inversion,
        over_range,
        sample_loss,
    )
    word = 0
    for i, flag in enumerate(flags):
        if flag is None:
            continue
        word |= 1 << (31 - i)  # enable bit: this indicator is being reported
        if flag:
            word |= 1 << (19 - i)  # indicator bit
    return word


def version_info_word(year: int, day_of_year: int, revision: int = 1) -> int:
    """Build the version packet's Year/Day/Revision/Type/ICD word.

    Year is stored as an offset from 2000 in 7 bits. Type and ICD version are
    both 0 for DIFI 1.x.
    """
    return (
        ((year - 2000) & 0x7F) << 25
        | (day_of_year & 0x1FF) << 16
        | (revision & 0x3F) << 10
        | (0 & 0xF) << 6  # type
        | (0 & 0x3F)  # ICD version
    )


def _fixed(value: float, radix: int) -> int:
    """Scale a real value to its fixed-point integer representation."""
    return int(round(value * (1 << radix)))


# --------------------------------------------------------------------------
# Packet builders
# --------------------------------------------------------------------------


class DifiEncoder:
    """Builds DIFI packets for one signal flow.

    Each packet type carries its own modulo-16 sequence counter, as required by
    the standard, so the three counters advance independently.
    """

    def __init__(
        self,
        stream_id: int,
        sample_rate: float,
        rf_freq: float,
        bit_depth: int = 16,
        bandwidth: Optional[float] = None,
        if_freq: float = 0.0,
        if_band_offset: float = 0.0,
        reference_level_dbm: float = 0.0,
        gain_stage1_db: float = 0.0,
        gain_stage2_db: float = 0.0,
        tsi: int = TSI_UTC,
        calibrated_time: Optional[bool] = False,
        valid_data: Optional[bool] = True,
        reference_lock: Optional[bool] = False,
    ) -> None:
        if bit_depth not in (4, 8, 12, 16):
            raise ValueError(f"bit_depth must be 4, 8, 12 or 16 (got {bit_depth})")

        self.stream_id = stream_id & 0xFFFFFFFF
        self.sample_rate = sample_rate
        self.rf_freq = rf_freq
        self.bit_depth = bit_depth
        # DIFI carries bandwidth explicitly; 80% of the sample rate is the
        # conventional default when the real analogue bandwidth is unknown.
        self.bandwidth = sample_rate * 0.8 if bandwidth is None else bandwidth
        self.if_freq = if_freq
        self.if_band_offset = if_band_offset
        self.reference_level_dbm = reference_level_dbm
        self.gain_stage1_db = gain_stage1_db
        self.gain_stage2_db = gain_stage2_db
        self.tsi = tsi
        self.calibrated_time = calibrated_time
        self.valid_data = valid_data
        self.reference_lock = reference_lock

        self._seq = {PKT_TYPE_DATA: 0, PKT_TYPE_CONTEXT: 0, PKT_TYPE_VERSION: 0}

    def _next_seq(self, pkt_type: int) -> int:
        seq = self._seq[pkt_type]
        self._seq[pkt_type] = (seq + 1) % 16
        return seq

    def _prologue(self, pkt_type: int, tsm: int, size_words: int, seconds: int, picoseconds: int) -> bytes:
        """Pack the 7 words every DIFI packet starts with."""
        return struct.pack(
            ">IIIIIQ",
            header_word(pkt_type, tsm, self.tsi, self._next_seq(pkt_type), size_words),
            self.stream_id,
            DIFI_OUI,  # padding bits and reserved bits are 0
            CLASS_DATA if pkt_type == PKT_TYPE_DATA else (CLASS_CONTEXT if pkt_type == PKT_TYPE_CONTEXT else CLASS_VERSION),
            seconds & 0xFFFFFFFF,
            picoseconds & 0xFFFFFFFFFFFFFFFF,
        )

    def data_packet(self, payload: bytes, seconds: int, picoseconds: int) -> bytes:
        """Wrap a block of interleaved IQ samples in a Signal Data packet.

        `payload` must already be big-endian interleaved I/Q at `bit_depth`, and
        must be a whole number of 32-bit words.
        """
        if not payload:
            # A payload-less data packet is structurally decodable but carries no
            # samples, which the standard does not allow; refuse it here rather
            # than emit something a conforming receiver has to reject.
            raise ValueError("a Signal Data packet must carry at least one sample")
        if len(payload) % 4:
            raise ValueError(f"payload must be a whole number of 32-bit words (got {len(payload)} bytes)")
        size_words = PROLOGUE_WORDS + len(payload) // 4
        if size_words > 0xFFFF:
            raise ValueError(f"packet size {size_words} words exceeds the 16-bit size field")
        # TSM = 0 on data packets: precise timestamps, not a coarse spectrum window.
        return self._prologue(PKT_TYPE_DATA, 0, size_words, seconds, picoseconds) + payload

    def context_packet(self, seconds: int, picoseconds: int, changed: bool = True) -> bytes:
        """Build the 27-word Standard Flow Signal Context packet.

        This is what tells the receiver how to interpret the data stream: centre
        frequency, sample rate, bandwidth, gain and sample format.
        """
        # TSM = 1 on context packets, per the standard.
        packet = self._prologue(PKT_TYPE_CONTEXT, 1, CONTEXT_WORDS, seconds, picoseconds)
        packet += struct.pack(
            ">IIQqqq",
            CIF0_CONTEXT_CHANGED if changed else CIF0_CONTEXT_UNCHANGED,
            REF_POINT_IF,
            _fixed(self.bandwidth, RADIX_FREQ),
            _fixed(self.if_freq, RADIX_FREQ),
            _fixed(self.rf_freq, RADIX_FREQ),
            _fixed(self.if_band_offset, RADIX_FREQ),
        )
        packet += struct.pack(
            ">hhhh",
            0,  # reference level word, bits 31-16 are reserved
            _fixed(self.reference_level_dbm, RADIX_GAIN),
            _fixed(self.gain_stage2_db, RADIX_GAIN),  # gain word, bits 31-16
            _fixed(self.gain_stage1_db, RADIX_GAIN),  # gain word, bits 15-0
        )
        packet += struct.pack(
            ">QqIIQ",
            _fixed(self.sample_rate, RADIX_FREQ),
            0,  # timestamp adjustment, femtoseconds
            0,  # timestamp calibration time
            state_event_word(
                calibrated_time=self.calibrated_time,
                valid_data=self.valid_data,
                reference_lock=self.reference_lock,
            ),
            payload_format_word(self.bit_depth),
        )
        assert len(packet) == CONTEXT_WORDS * 4, len(packet)
        return packet

    def version_packet(self, seconds: int, picoseconds: int, changed: bool = True) -> bytes:
        """Build the 11-word Version Flow Signal Context packet.

        Announces which revision of VITA 49 and of the DIFI ICD this flow obeys.
        """
        now = time.gmtime(seconds)
        packet = self._prologue(PKT_TYPE_VERSION, 1, VERSION_WORDS, seconds, picoseconds)
        packet += struct.pack(
            ">IIII",
            CIF0_VERSION_CHANGED if changed else CIF0_VERSION_UNCHANGED,
            CIF1_VERSION,
            V49_SPEC_VERSION,
            version_info_word(now.tm_year, now.tm_yday),
        )
        assert len(packet) == VERSION_WORDS * 4, len(packet)
        return packet


# --------------------------------------------------------------------------
# Signal generation
# --------------------------------------------------------------------------


class IQSource:
    """Synthesises interleaved big-endian IQ samples.

    Samples are generated once into a cyclic buffer and then sliced per packet,
    which keeps the per-packet cost to a memory copy. For the tone this means
    the requested frequency is snapped to the nearest bin that fits the buffer
    a whole number of times, so the output stays phase continuous forever.
    """

    def __init__(
        self,
        kind: str,
        sample_rate: float,
        bit_depth: int,
        amplitude: float = 0.5,
        tone_freq: float = 100_000.0,
        sweep_span: Optional[float] = None,
        buffer_samples: int = 65536,
        seed: Optional[int] = None,
    ) -> None:
        self.kind = kind
        self.sample_rate = sample_rate
        self.bit_depth = bit_depth
        self.bytes_per_sample = 2 * (bit_depth // 8)
        self.actual_tone_freq = tone_freq

        full_scale = (1 << (bit_depth - 1)) - 1
        peak = max(1.0, amplitude * full_scale)
        rng = random.Random(seed)
        n = buffer_samples

        pairs: list[tuple[int, int]] = []
        if kind == "tone":
            # Snap to the nearest bin so the buffer wraps without a phase jump.
            bins = max(1, round(tone_freq * n / sample_rate))
            self.actual_tone_freq = bins * sample_rate / n
            step = 2.0 * math.pi * bins / n
            pairs = [
                (int(round(peak * math.cos(step * i))), int(round(peak * math.sin(step * i))))
                for i in range(n)
            ]
        elif kind == "sweep":
            # Linear chirp across the buffer, repeating each time it wraps.
            span = sample_rate * 0.8 if sweep_span is None else sweep_span
            start = -span / 2.0
            rate = span / n  # frequency step per sample
            phase = 0.0
            for i in range(n):
                freq = start + rate * i
                phase += 2.0 * math.pi * freq / sample_rate
                pairs.append((int(round(peak * math.cos(phase))), int(round(peak * math.sin(phase)))))
        elif kind == "noise":
            sigma = peak / 3.0  # keep ~3 sigma inside full scale
            limit = full_scale
            for _ in range(n):
                i_val = max(-limit, min(limit, int(round(rng.gauss(0.0, sigma)))))
                q_val = max(-limit, min(limit, int(round(rng.gauss(0.0, sigma)))))
                pairs.append((i_val, q_val))
        elif kind == "zero":
            pairs = [(0, 0)] * n
        else:
            raise ValueError(f"unknown signal kind: {kind!r}")

        fmt = ">" + ("h" if bit_depth == 16 else "b") * (2 * n)
        flat: list[int] = []
        for i_val, q_val in pairs:
            flat.append(i_val)
            flat.append(q_val)
        self._buf = struct.pack(fmt, *flat)
        self._buf_samples = n
        self._pos = 0
        self._random_offset = kind == "noise"
        self._rng = rng

    def read(self, num_samples: int) -> bytes:
        """Return `num_samples` interleaved IQ samples as bytes."""
        nbytes = num_samples * self.bytes_per_sample
        if self._random_offset:
            # Decorrelate successive packets so the noise does not audibly loop.
            self._pos = self._rng.randrange(self._buf_samples) * self.bytes_per_sample

        end = self._pos + nbytes
        if end <= len(self._buf):
            chunk = self._buf[self._pos : end]
            self._pos = end % len(self._buf)
            return chunk

        # Wrap around the end of the cyclic buffer.
        parts = [self._buf[self._pos :]]
        remaining = nbytes - (len(self._buf) - self._pos)
        while remaining >= len(self._buf):
            parts.append(self._buf)
            remaining -= len(self._buf)
        parts.append(self._buf[:remaining])
        self._pos = remaining
        return b"".join(parts)


# --------------------------------------------------------------------------
# Transmit loop
# --------------------------------------------------------------------------


def _split_host_port(value: str, default_port: int = 4991) -> tuple[str, int]:
    """Parse `host:port`, `host` or `[v6]:port` into a (host, port) pair."""
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        return host, int(rest.lstrip(":")) if rest.lstrip(":") else default_port
    if value.count(":") == 1:
        host, _, port = value.partition(":")
        return host or "127.0.0.1", int(port)
    return value, default_port


def transmit(args: argparse.Namespace) -> int:
    host, port = _split_host_port(args.dest)

    bytes_per_sample = 2 * (args.bit_depth // 8)
    samples_per_packet = args.samples_per_packet
    if samples_per_packet is None:
        # Fill an Ethernet frame without fragmenting.
        usable = MTU_SAFE_UDP_BYTES - PROLOGUE_WORDS * 4
        samples_per_packet = (usable // bytes_per_sample) // 2 * 2
    payload_bytes = samples_per_packet * bytes_per_sample
    if payload_bytes % 4:
        raise SystemExit(
            f"--samples-per-packet {samples_per_packet} at {args.bit_depth}-bit gives a "
            f"{payload_bytes} byte payload, which is not a whole number of 32-bit words"
        )

    udp_bytes = PROLOGUE_WORDS * 4 + payload_bytes
    packet_rate = args.sample_rate / samples_per_packet

    encoder = DifiEncoder(
        stream_id=args.stream_id,
        sample_rate=args.sample_rate,
        rf_freq=args.rf_freq,
        bit_depth=args.bit_depth,
        bandwidth=args.bandwidth,
        if_freq=args.if_freq,
        reference_level_dbm=args.reference_level,
        gain_stage1_db=args.gain,
        tsi=TSI_NAMES[args.tsi],
        calibrated_time=args.calibrated_time,
        reference_lock=args.reference_lock,
    )
    source = IQSource(
        kind=args.signal,
        sample_rate=args.sample_rate,
        bit_depth=args.bit_depth,
        amplitude=args.amplitude,
        tone_freq=args.tone_freq,
        seed=args.seed,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.send_buffer:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, args.send_buffer)
    dest = (host, port)

    print(f"DIFI transmitter -> {host}:{port}")
    print(f"  stream ID        0x{encoder.stream_id:08X}")
    print(f"  sample rate      {args.sample_rate:,.0f} Sa/s   bandwidth {encoder.bandwidth:,.0f} Hz")
    print(f"  RF reference     {args.rf_freq:,.0f} Hz")
    print(f"  format           {args.bit_depth}-bit complex cartesian, link efficient")
    print(f"  packets          {samples_per_packet} samples, {udp_bytes} B UDP, {packet_rate:,.1f} pkt/s")
    if args.signal == "tone":
        print(f"  signal           tone at {source.actual_tone_freq:,.1f} Hz offset, amplitude {args.amplitude}")
    else:
        print(f"  signal           {args.signal}, amplitude {args.amplitude}")
    if udp_bytes > MTU_SAFE_UDP_BYTES:
        print(f"  note: {udp_bytes} B exceeds the {MTU_SAFE_UDP_BYTES} B non-fragmenting limit "
              f"for a 1500 B MTU; enable jumbo frames or lower --samples-per-packet")
    print(f"  context every    {args.context_interval}s, version every {args.version_interval}s")
    print()

    # Timestamps advance from a single wall-clock anchor by exact sample counts,
    # so they stay sample-accurate instead of inheriting the scheduler's jitter.
    start_wall = time.time()
    start_sec = int(start_wall)
    start_ps = int((start_wall - start_sec) * PICOSECONDS_PER_SECOND)
    start_perf = time.perf_counter()

    def timestamp_for(sample_index: int) -> tuple[int, int]:
        total_ps = start_ps + sample_index * PICOSECONDS_PER_SECOND // int(args.sample_rate)
        return start_sec + total_ps // PICOSECONDS_PER_SECOND, total_ps % PICOSECONDS_PER_SECOND

    samples_sent = 0
    packets_sent = 0
    bytes_sent = 0
    next_context = 0.0
    next_version = 0.0
    last_report = 0.0

    try:
        while True:
            elapsed = time.perf_counter() - start_perf
            if args.duration and elapsed >= args.duration:
                break

            seconds, picoseconds = timestamp_for(samples_sent)

            # Context and version packets are interleaved into the data stream.
            if elapsed >= next_version:
                sock.sendto(encoder.version_packet(seconds, picoseconds, changed=next_version == 0.0), dest)
                next_version = elapsed + args.version_interval
            if elapsed >= next_context:
                sock.sendto(encoder.context_packet(seconds, picoseconds, changed=next_context == 0.0), dest)
                next_context = elapsed + args.context_interval

            packet = encoder.data_packet(source.read(samples_per_packet), seconds, picoseconds)
            sock.sendto(packet, dest)

            samples_sent += samples_per_packet
            packets_sent += 1
            bytes_sent += len(packet)

            if not args.burst:
                # Pace against the stream clock rather than sleeping a fixed
                # amount, so scheduling jitter cannot accumulate into drift.
                target = samples_sent / args.sample_rate
                lag = target - (time.perf_counter() - start_perf)
                if lag > 0:
                    time.sleep(lag)

            if args.report_interval and elapsed - last_report >= args.report_interval:
                rate = bytes_sent * 8 / elapsed / 1e6 if elapsed else 0.0
                print(
                    f"[{elapsed:7.1f}s] {packets_sent:,} data packets, "
                    f"{samples_sent:,} samples, {rate:,.2f} Mb/s"
                )
                last_report = elapsed
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        sock.close()

    elapsed = time.perf_counter() - start_perf
    print()
    print(f"sent {packets_sent:,} data packets / {samples_sent:,} samples in {elapsed:.2f}s")
    if elapsed > 0:
        achieved = samples_sent / elapsed
        print(f"  effective rate {achieved:,.0f} Sa/s ({achieved / args.sample_rate * 100:.1f}% of requested)")
        print(f"  {bytes_sent * 8 / elapsed / 1e6:,.2f} Mb/s on the wire")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transmit a DIFI / VITA 49.2 signal flow over UDP.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dest", default="127.0.0.1:4991", help="Destination as host:port")
    parser.add_argument("--stream-id", type=lambda v: int(v, 0), default=0, help="32-bit stream ID")

    signal = parser.add_argument_group("signal")
    signal.add_argument("--sample-rate", type=float, default=1e6, help="Sample rate in Sa/s")
    signal.add_argument("--rf-freq", type=float, default=1.95e9, help="RF reference frequency in Hz")
    signal.add_argument("--if-freq", type=float, default=0.0, help="IF reference frequency in Hz")
    signal.add_argument("--bandwidth", type=float, default=None, help="Bandwidth in Hz (default: 80%% of sample rate)")
    signal.add_argument("--bit-depth", type=int, choices=(8, 16), default=16, help="Bits per I and per Q component")
    signal.add_argument("--gain", type=float, default=0.0, help="Stage 1 gain in dB, reported in the context packet")
    signal.add_argument("--reference-level", type=float, default=0.0, help="Reference level in dBm")
    signal.add_argument(
        "--signal", choices=("tone", "sweep", "noise", "zero"), default="tone", help="Synthetic payload to transmit"
    )
    signal.add_argument("--tone-freq", type=float, default=100e3, help="Tone offset from centre in Hz")
    signal.add_argument("--amplitude", type=float, default=0.5, help="Peak amplitude as a fraction of full scale")
    signal.add_argument("--seed", type=int, default=None, help="Seed for the noise generator")
    signal.add_argument(
        "--calibrated-time",
        action="store_true",
        help="Assert the calibrated-time indicator in the context packet",
    )
    signal.add_argument(
        "--reference-lock",
        action="store_true",
        help="Assert the reference-lock indicator in the context packet",
    )

    stream = parser.add_argument_group("stream")
    stream.add_argument(
        "--samples-per-packet", type=int, default=None, help="Samples per data packet (default: fill a 1500 B MTU)"
    )
    stream.add_argument("--context-interval", type=float, default=1.0, help="Seconds between context packets")
    stream.add_argument("--version-interval", type=float, default=10.0, help="Seconds between version packets")
    stream.add_argument("--tsi", choices=tuple(TSI_NAMES), default="utc", help="Integer timestamp epoch")
    stream.add_argument("--duration", type=float, default=0.0, help="Seconds to transmit, 0 for until interrupted")
    stream.add_argument("--burst", action="store_true", help="Send as fast as possible instead of pacing to the sample rate")
    stream.add_argument("--send-buffer", type=int, default=0, help="SO_SNDBUF in bytes, 0 to leave the OS default")
    stream.add_argument("--report-interval", type=float, default=2.0, help="Seconds between progress lines, 0 to disable")

    args = parser.parse_args()
    if args.sample_rate <= 0:
        parser.error("--sample-rate must be positive")
    if not 0.0 <= args.amplitude <= 1.0:
        parser.error("--amplitude must be between 0 and 1")
    if args.samples_per_packet is not None and args.samples_per_packet <= 0:
        parser.error("--samples-per-packet must be positive")
    if args.duration < 0:
        parser.error("--duration must not be negative")

    sys.exit(transmit(args))


if __name__ == "__main__":
    main()
