# `src/difi/difi_tx.py` / `src/difi/difi_rx.py` — DIFI / VITA 49.2 digitised IF

[← README](../README.md) · [Console](console.md) · [CLI](cli.md) · **DIFI**

Two standalone applications that carry a digitised RF/IF stream over UDP using
the **DIFI 1.x** packet set (IEEE-ISTO Std 4900-2021), the ground-segment profile
of **VITA 49.2 (VRT)**. `difi_tx.py` synthesises and transmits a flow; `difi_rx.py`
receives it, decodes every field, and validates it against the standard.

Both are pure standard library — nothing to add to `config/requirements.txt`.

```bash
# terminal 1 — receive and validate
python src/difi/difi_rx.py --bind 0.0.0.0:4991 --measure-signal

# terminal 2 — transmit a 1 MSPS 16-bit tone for 10 seconds
python src/difi/difi_tx.py --dest 127.0.0.1:4991 --duration 10
```

## The three packet types

DIFI constrains VITA 49.2 to exactly three packets, all big-endian, all with a
shared 7-word prologue (header, stream ID, two Class ID words, and a 32-bit
integer plus 64-bit picosecond timestamp):

| Packet | Type | Info / Packet class | Size | Carries |
|---|---|---|---|---|
| Standard Flow Signal Data | `0x1` | `0x0000` / `0x0000` | 7 + N words | Interleaved complex IQ samples |
| Standard Flow Signal Context | `0x4` | `0x0000` / `0x0001` | 27 words | RF/IF frequency, sample rate, bandwidth, gain, reference level, sample format |
| Version Flow Signal Context | `0x5` | `0x0001` / `0x0004` | 11 words | VITA 49 spec revision and DIFI ICD version |

Every Class ID carries the DIFI OUI `0x6A621E`. TSF is fixed at `0x2` (real time,
picoseconds); TSI selects the epoch and must not be zero. The transmitter
interleaves context and version packets into the data stream on their own timers,
and each packet type keeps an independent modulo-16 sequence counter as the
standard requires.

## Transmitter

```bash
python src/difi/difi_tx.py --dest 127.0.0.1:4991 \
    --sample-rate 1e6 --rf-freq 1.95e9 --bit-depth 16 \
    --signal tone --tone-freq 250e3 --amplitude 0.5 --duration 10
```

| Option | Purpose |
|---|---|
| `--dest host:port` | Where to send (default `127.0.0.1:4991`) |
| `--stream-id` | 32-bit stream ID, accepts `0x` hex |
| `--sample-rate`, `--rf-freq`, `--if-freq`, `--bandwidth` | Flow parameters advertised in the context packet; bandwidth defaults to 80% of the sample rate |
| `--bit-depth {8,16}` | Bits per I and per Q component |
| `--signal {tone,sweep,noise,zero}` | Synthetic payload; `--tone-freq` and `--amplitude` shape it |
| `--samples-per-packet` | Must be positive; defaults to filling a 1500-byte MTU without fragmenting (360 samples at 16-bit) |
| `--context-interval`, `--version-interval` | Seconds between context / version packets |
| `--gain`, `--reference-level`, `--calibrated-time`, `--reference-lock` | Values reported in the context packet |
| `--burst` | Send flat out instead of pacing to the sample rate |
| `--tsi {utc,gps,posix}` | Integer timestamp epoch |

Timestamps advance from one wall-clock anchor by exact sample counts, so they stay
sample-accurate rather than inheriting scheduler jitter, and pacing targets the
stream clock so jitter cannot accumulate into drift.

The transmitter refuses input that would put a non-conforming packet on the wire
rather than sending it and leaving the receiver to complain: `--samples-per-packet`
must be positive, `--duration` must not be negative, `--amplitude` must be within
0..1, and a payload that is empty or not a whole number of 32-bit words is
rejected by the encoder itself. A packet size that would exceed 1500 bytes is
allowed but warned about, since it needs jumbo frames to avoid fragmenting.

## Receiver

```bash
python src/difi/difi_rx.py --bind 0.0.0.0:4991 --measure-signal --duration 30
```

| Option | Purpose |
|---|---|
| `--bind host:port` | Address to listen on (default `0.0.0.0:4991`) |
| `--stream-id` | Only accept one stream ID; everything else is counted and dropped |
| `--measure-signal` | Decode IQ payloads to report RMS and peak in dBFS (costs CPU at high rates) |
| `--verbose` / `--quiet` | Print every packet / suppress context and version dumps |
| `--duration`, `--max-errors`, `--report-interval`, `--recv-buffer` | Run length, error print cap, progress cadence, socket buffer |

It tracks each stream ID separately and reports, per packet type, the count,
how many conformed, sequence gaps with an estimate of packets lost, timestamp
regressions, throughput, and the decoded contents of the most recent context
packet. Sample bit depth is taken from that context packet, so the receiver
adapts to the flow without being told.

Exit status makes it usable as a conformance check in a script:

| Code | Meaning |
|---|---|
| `0` | Every packet conformed |
| `1` | No DIFI packets received |
| `2` | One or more spec violations |

Validation covers the packet type, Class ID present bit, reserved bits, TSM, TSI,
TSF, OUI, information and packet class codes, declared size against the real
datagram length, fractional timestamps below one second, CIF0/CIF1, reference
point, the full data payload format (packing method, real/complex type, item
format, tag sizes, item sizes), and frequency sanity — plus sequence continuity
and timestamp monotonicity across packets.

## What a run looks like

Transmitting a 1 MSPS 16-bit tone for three seconds:

```
$ python src/difi/difi_tx.py --dest 127.0.0.1:4991 --duration 3 --tone-freq 250e3
DIFI transmitter -> 127.0.0.1:4991
  stream ID        0x00000000
  sample rate      1,000,000 Sa/s   bandwidth 800,000 Hz
  RF reference     1,950,000,000 Hz
  format           16-bit complex cartesian, link efficient
  packets          360 samples, 1468 B UDP, 2,777.8 pkt/s
  signal           tone at 250,000.0 Hz offset, amplitude 0.5
  context every    1.0s, version every 10.0s

sent 8,334 data packets / 3,000,240 samples in 3.00s
  effective rate 999,973 Sa/s (100.0% of requested)
  32.62 Mb/s on the wire
```

and the matching receiver:

```
$ python src/difi/difi_rx.py --bind 0.0.0.0:4991 --measure-signal
====================================================================
DIFI receive summary
====================================================================
stream 0x00000000
  data          8,334  conforming      8,334
  context           3  conforming          3
  version           1  conforming          1
  samples   3,000,240   payload 12,000,960 B
  loss              0 packets (0.000% of expected)
  received 8,338 packets in 3.00s (32.63 Mb/s)
  signal   RMS  -9.03 dBFS   peak  -6.02 dBFS   (16-bit)
  last context:
    RF reference   1,950,000,000 Hz
    IF reference   0 Hz
    sample rate    1,000,000 Sa/s
    bandwidth      800,000 Hz
    gain           0.00 / 0.00 dB
    reference lvl  0.00 dBm
    format         16-bit complex cartesian, link efficient
  total spec violations: 0

PASS: every packet conformed to DIFI 1.x
```

The reported levels are the analytically correct ones for `--amplitude 0.5`: a peak
of −6.02 dBFS, and an RMS 3.01 dB below that for a complex tone.

When packets go missing, the gap is named rather than just counted:

```
  [data seq 7] data sequence jumped 3 -> 7 (3 packets lost or reordered)
...
  data              9  conforming          8  sequence gaps 1
  loss              3 packets (25.000% of expected)

FAIL: 1 spec violation(s) across 1 stream(s)
```

## Conformance

Given the same flow parameters, the encoder reproduces the DIFI Consortium's
published reference context packet **byte for byte** — all 27 words, including the
header, both Class ID words, `CIF0 = 0xFBB98000`, the 2<sup>-20</sup> fixed-point
frequency scaling, 2<sup>-7</sup> gain scaling, the State and Event Indicators
word, and the payload format word `0xA00001C7` for 8-bit samples (`0xA00003CF` at
16-bit).

The receiver's checks were verified against deliberately corrupted packets —
wrong OUI, wrong class codes, bad CIF0, wrong reference point, `TSF` set to sample
count, `TSI = 0`, size-field mismatch, reserved bits set, `TSM` wrong for the
packet type, out-of-range fractional timestamps, processing-efficient packing,
bad version CIF1, out-of-range RF frequency, truncated and non-word-aligned
datagrams — each of which is reported with the specific field and expected value.

## Notes and limits

- The two files are deliberately standalone: each carries its own copy of the wire
  constants, so either one can be read, copied, or run on its own machine without
  the other. There is no shared module to keep in step.
- Sustained throughput is bounded by Python: roughly 30–60 Mb/s on one core, which
  covers 1 MSPS at 16-bit comfortably. For line-rate work the packet framing here
  ports directly onto the UDP source/sink already in the neighbouring `SISO2` C++
  project.
- The transmitter synthesises IQ locally; it is not wired to a radio. Signals are
  generated once into a cyclic buffer and sliced per packet, so the tone frequency
  is snapped to the nearest bin that wraps cleanly, keeping it phase continuous.
- If the receiver reports loss on a local loopback run, the usual cause is the
  kernel socket buffer overflowing rather than a real fault — raise it with
  `--recv-buffer` (the receiver prints the size the OS actually granted at
  startup), or drop `--measure-signal`, which is the most expensive work it does.
- `--burst` sends as fast as the CPU allows and will normally overrun the
  receiver. It is for stressing the decoder, not for a clean loss measurement.
- DIFI 1.2's command packets (flow control, packet classes `0x0005`/`0x0006`) are
  not implemented — only the three packets of the 1.0/1.1 data plane.

---

[← back to the README](../README.md)
