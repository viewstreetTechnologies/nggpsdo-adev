# NGGPSDO SSE protocol

This document describes the NGGPSDO frequency measurement stream consumed by
NGGPSDO ADEV Monitor. It is intended for developers building their own clients.
The examples are illustrative, not captured measurements.

## Connection

Enable `network.sse.enable` on the hardware and use its configured SSE port
(normally 80). The built-in endpoint is HTTP, not HTTPS:

```text
http://DEVICE-IP:80/events?eventType=fc
```

Requests use HTTP GET. A successful response has status `200` and content type
`text/event-stream`. The response remains open for streaming. For example:

```powershell
curl.exe -N "http://DEVICE-IP/events?eventType=fc"
```

| Parameter | Meaning |
|---|---|
| `eventType` | Required. Use the case-sensitive name `fc`, or numeric alias `1`. Older firmware may require `1`. |
| `afterEventId` | Optional unsigned decimal 64-bit event ID, from `0` to `18446744073709551615`. Replay starts **after** this ID. Omit for live-only delivery. Use `0` to request available history from its beginning. |
| `replayIntervalMs` | Historical delivery interval in milliseconds, from `1` to `2147483647`; if omitted, 1000. This controls replay pacing, not measurement timing or live acquisition. |

The `Last-Event-ID` request header supplies a resume cursor and takes precedence
over `afterEventId`. Use ordinary decimal digits without separators. The built-in
parser does not support percent-encoded parameters, duplicate parameters, or
unknown query parameters. Event selectors contain at most 40 ASCII letters,
digits, hyphens, underscores or periods; entirely numeric selectors are IDs.

## Hardware history and resuming

With hardware SD journaling enabled, acquisition and retention operate independently
of the desktop client. Days or weeks of retained history can be retrieved,
depending on configured retention and storage. The computer does not need to
remain connected or running during capture.

```powershell
# Retrieve retained history, delivered at 100 ms intervals, then follow live data.
curl.exe -N "http://DEVICE-IP/events?eventType=fc&afterEventId=0&replayIntervalMs=100"

# Continue after event 41001, for example following a connection failure.
curl.exe -N -H "Last-Event-ID: 41001" "http://DEVICE-IP/events?eventType=fc&replayIntervalMs=100"
```

Replay transitions to live delivery after the client catches up. It does not
change original measurement timestamps. History that is missing or no longer
retained is skipped to the next available ID; a resume request does not guarantee
that every intervening event remains available. Detect gaps rather than treating
the stream as a lossless transport.

Persist a cursor in your own client if resuming across client restarts is needed.
NGGPSDO ADEV Monitor keeps its automatic reconnect cursor only within the active
connection session. Starting a fresh connection clears its analysis window.

After device power loss, valid journal records are recovered; incomplete or corrupt
tails are discarded. Unflushed records can be lost. No samples are acquired while
the device is off. Persistent journal IDs resume above the valid recovered history;
IDs assigned without persistent storage can repeat after power loss. Do not use an
event ID alone as proof of measurement continuity.

## Event framing

Each frequency event consists of an ID, event name and JSON payload, followed by
a blank line. The firmware emits the following shape (JSON is one physical line):

```text
id: 41001
event: fc
data: {"sampleNumber":"123","endTimeUnixMs":1760000000000,"intervalUs":4000000,"frequencyHz":10000000.0125,"nominalHz":10000000,"flags":455,"tickError":1,"correctionPs":0,"ocxoTemperatureC":49.25,"controlVoltageV":2.4815,"controllerFrequencyHz":10000000.0002,"controllerFilterLevel":5}

```

Lines beginning with `:` are comments, such as `: heartbeat`. Ignore them; they
are not measurements. Handle blank-line event boundaries and both LF and CRLF
line endings. An SSE-capable decoder should join multiple `data:` lines with
newlines. Ignore unknown event types and allow additional JSON fields.

The SSE `id` is the journal/delivery cursor, expressed as decimal text. The JSON
`sampleNumber` is a separate acquisition sequence that resets at device boot and
advances even when publishing fails. Keep IDs as strings or full-width integers;
JavaScript `Number` cannot exactly represent every unsigned 64-bit value.

## Frequency message fields

| Field | JSON type | Meaning |
|---|---|---|
| `sampleNumber` | String | Unsigned decimal acquisition sequence. Preserve its integer precision. |
| `endTimeUnixMs` | Integer | UTC measurement-end time, milliseconds since the Unix epoch. Zero when unavailable; consult flag 4. |
| `intervalUs` | Integer | Nominal acquisition gate in microseconds. Currently `4000000`: four reference PPS periods. |
| `frequencyHz` | Number | Unaveraged input frequency in Hz, including available timing correction. Use this for stability analysis. |
| `nominalHz` | Number | Nominal input frequency in Hz, currently `10000000`. |
| `flags` | Integer | Validity and discontinuity bit mask; see below. |
| `tickError` | Integer | Signed counter error over the gate, in timer ticks. One tick corresponds to 5 ns at 200 MHz. |
| `correctionPs` | Integer | Difference between TIM-TP endpoint corrections, in picoseconds. Zero when unavailable; consult flag 2. |
| `ocxoTemperatureC` | Number or `null` | OCXO temperature in degrees Celsius; usable when flag 64 is set. May be absent on older firmware. |
| `controlVoltageV` | Number or `null` | Calculated control voltage in volts; usable when flag 128 is set. May be absent on older firmware. |
| `controllerFrequencyHz` | Number or `null` | Hardware rolling-average frequency in Hz; usable when flag 256 is set. May be absent on older firmware. |
| `controllerFilterLevel` | Integer | Filter level associated with a valid controller frequency. Interpret only when flag 256 and a valid frequency are present. May be absent on older firmware. |

Temperature is read at callback processing time, not exactly at the gate edge.
Control voltage is calculated from cached DAC settings before the corresponding
controller adjustment. It is neither an ADC measurement nor an average over the
gate. Manual OCXOs may not provide absolute control voltage. Missing telemetry
must remain unavailable, not be replaced with zero or a stale previous value.

The rolling controller frequency shares the raw sample timestamp but represents
the active hardware averaging window. It is distinct from `frequencyHz` and must
not be substituted into raw-sample ADEV/MDEV/HDEV calculations.

## Flag bits

Test bits independently; do not compare the complete mask to one fixed value.

| Decimal | Hex | Name | Meaning |
|---|---|---|---|
| 1 | `0x001` | Valid | Corrected frequency available and no overflow/tolerance flags set by the sample builder. See the qualification below. |
| 2 | `0x002` | CorrectionValid | TIM-TP correction available. |
| 4 | `0x004` | TimeValid | UTC measurement timestamp available. |
| 8 | `0x008` | Discontinuity | A continuity break occurs before this sample; begin a new run. |
| 16 | `0x010` | CounterOverflow | Reserved overflow indication; the current FC listener does not expose or set this condition. |
| 32 | `0x020` | ToleranceExceeded | Reserved tolerance indication; the current FC listener does not expose or set this condition. |
| 64 | `0x040` | TemperatureValid | Temperature read succeeded. |
| 128 | `0x080` | ControlVoltageValid | Calculated control voltage available. |
| 256 | `0x100` | ControllerFrequencyValid | Rolling hardware frequency available for this tick. |

The current stream is published before FC tolerance rejection. Flag 1 therefore
does not guarantee that a measurement passed the hardware controller's tolerance
filter or that GNSS lock quality has been independently verified. Clients must
apply their own acceptance policy. Optional telemetry validity does not establish
frequency validity. Reject non-finite numeric values and invalid gates or nominal
frequencies before analysis.

## Stability analysis and continuity

Calculate fractional frequency from the raw measurement:

```text
y = (frequencyHz - nominalHz) / nominalHz
```

For the current four-second gate, the nominal sample rate is 0.25 Hz. Use
measurement timestamps and `intervalUs`, never network arrival intervals or
replay pacing, to determine averaging times.

NGGPSDO ADEV Monitor requires flags 1 and 4, a positive timestamp, no flags 16 or
32, and an absolute frequency offset within its configured rejection threshold.
It begins a new run on an invalid sample, flag 8, a nonconsecutive `sampleNumber`,
a nominal-frequency or gate change, or timestamp spacing outside the gate duration
plus/minus 100 ms. Monitor event-ID gaps as an additional delivery diagnostic;
the current desktop analysis does not use them as its continuity test.

Only form stability terms within continuous runs. A post-gap valid sample can
start a new run; do not discard it merely because flag 8 is present. GPS PPS
reference noise and the GPSDO control loop contribute to the measured stability.

## Errors and reconnects

| HTTP status | Typical meaning |
|---|---|
| `400` | Malformed request, missing selector, invalid or duplicate parameter/header, or unsupported request body. |
| `404` | Unknown event name/ID or endpoint. |
| `405` | Unsupported HTTP method. |
| `431` | Request headers exceed the built-in parser's 1024-byte bound. |
| `503` | Service/client capacity unavailable. |

The device can close a connection on output stalls or other transport failures.
Reconnect with a bounded delay and the last successfully handled event ID.
Heartbeats do not advance that cursor. The desktop monitor retries after three
seconds and requests `Last-Event-ID` after it has received an FC event.

The built-in service does not implement TLS or authentication. Use it on a trusted
network or provide an appropriately secured external network boundary.

## Compatibility

The current FC persistent journal format is version 5. It includes raw frequency,
OCXO telemetry and controller-frequency fields. This is a hardware storage format
version, not a version field in each JSON message. Older clients can ignore added
fields; newer clients should accept missing optional telemetry.

Older firmware may require numeric `eventType=1`. Firmware changes that alter the
binary journal layout can discard incompatible retained history during recovery.
Preserve needed measurements before such upgrades. No fixed firmware release
number is implied by this document; confirm the fields supported by the device.
