# Canon DR-C225W II cgiscsi Protocol Specification

This document describes the network SCSI-over-HTTP protocol used by the Canon
imageFORMULA DR-C225W II in Wi-Fi mode at `POST /cgi-bin/cgiscsi`.

The highest-confidence findings come from Canon's macOS driver package
`DR-C225_Driver_V.2.2.25.1031forMac.pkg`, especially:

- `DRNetworkScanner.bundle/Contents/MacOS/DRNetworkScanner`
- `DRC225.ds/Contents/MacOS/DRC225`

The Windows SP5 bundle was downloaded and inventoried, but the available local
tools did not extract a deeper network driver DLL from its nested installers.
No Ghidra or `class-dump` binary was available in this environment, so the
static analysis used `otool`, symbols, Objective-C metadata, strings, and
targeted disassembly.

Live validation against scanner `<scanner-ip>` confirmed known-IP
identification/status, non-image reads, ADF feed/discharge, SET/GET WINDOW, and
duplex JPEG image acquisition to a two-page PDF. Earlier conservative `SCAN`
attempts returned an invalid-parameter sense condition until the SET WINDOW
defaults were corrected to match Canon's driver more closely; one malformed
scan probe required physical scanner recovery.

## Confidence Legend

- **confirmed-wire**: observed on the live scanner and in Canon driver code.
- **confirmed-driver**: decoded from Canon driver code, not independently
  exercised during this task.
- **inferred**: aligned with SANE `canon_dr` prior art and driver call names,
  but not fully decoded or tested here.
- **unknown**: field exists but semantics are not yet known.

## Device Discovery and INQUIRY

The decoded Canon network plugin does not perform broadcast discovery itself.
`CNetworkScanner::CreateScannerList` accepts a caller-supplied
`tagNetworkInterfaceDesc`-like record, copies 0x2a bytes to a `CFData`, and
later constructs a scanner from it:

| Offset | Size | Meaning |
| --- | ---: | --- |
| 0x00 | variable | NUL-terminated UTF-8 IP address or host string |
| 0x28 | 2 | TCP port, native-endian in the macOS caller data |

For a clean-room implementation, discovery can therefore start with a known IP
or another external discovery mechanism, then confirm the target by issuing
SCSI INQUIRY over `cgiscsi`.

The live scanner was also reachable by the Bonjour host
`<scanner-bonjour-name>.local`. This is useful as a candidate source, but the Canon
macOS network plugin code analyzed here still expects an externally supplied
host record before it constructs cgiscsi requests.

The Python harness includes this conservative discovery strategy in
`harness/discover.py`: given one or more candidate hosts or CIDR ranges, it
POSTs a padded SCSI INQUIRY to `/cgi-bin/cgiscsi`, parses the standard INQUIRY
identity fields, and reports hosts that return non-empty vendor/product data.
This is not Canon broadcast discovery; it is a network scan plus protocol-level
confirmation.

Example:

```sh
cd harness
uv run python discover.py --cidr 192.168.1.0/24 --timeout 0.5 --workers 32
uv run python discover.py --candidate <scanner-ip>
```

Live confirmed INQUIRY request:

```text
POST http://<scanner-ip>/cgi-bin/cgiscsi
Content-type: application/x-www-form-urlencoded

c=120000006000000000000000&i&dl=96
```

The successful response was 114 bytes: 96 bytes of INQUIRY data followed by an
18-byte cgiscsi trailer. The INQUIRY data identified:

```text
Peripheral type: scanner (0x06)
Vendor:          CANON
Product:         DR-C225
Revision:        1.06
Date string:     20140609
```

## HTTP Envelope

**Status:** confirmed-wire, confirmed-driver.

The endpoint is:

```text
POST /cgi-bin/cgiscsi HTTP/1.1
```

Canon's URL builder formats:

```text
%@://%@/cgi-bin/cgiscsi
```

The scheme is `https` if the stored port equals 443, otherwise `http`. The
driver does not format an explicit port into this URL string.

Driver headers:

| Header | Value |
| --- | --- |
| `Accept` | `*/*` |
| `Content-type` | `application/x-www-form-urlencoded` |
| `Content-length` | Decimal length of the ASCII form body |

The HTTP body is ASCII/UTF-8 form text. It is not a binary POST body.

The scanner's live HTTP server identified itself as `lighttpd/1.4.39`.

## cgiscsi Request Body

**Status:** confirmed-wire, confirmed-driver.

The form body has one of these forms:

```text
c=<hex-cdb>&i&dl=<expected-data-in-length>
c=<hex-cdb>&o&d=<hex-data-out>&dl=<data-out-length>
c=<hex-cdb>&i&dl=<expected-data-in-length>&a=<client-mac>
c=<hex-cdb>&o&d=<hex-data-out>&dl=<data-out-length>&a=<client-mac>
```

Fields:

| Field | Meaning |
| --- | --- |
| `c` | SCSI CDB bytes, lower-case hex, two digits per byte |
| `&i` | Direction marker for data-in command |
| `&o` | Direction marker for data-out command |
| `d` | Data-out bytes, lower-case hex, two digits per byte |
| `dl` | Decimal byte count for the selected data phase |
| `a` | Optional client MAC address as 12 lowercase hex digits |

Canon's `toAscii:size:` uses the format string `%02.2hhx`, so every byte is
encoded as fixed-width lowercase hex. The optional MAC address is read from
macOS `en0` and formatted with no separators.

Canon's macOS driver normally sends 12 CDB bytes because it strips a 12-byte
local command container from a 0x18-byte staged command buffer. Standard-length
CDBs were accepted for some live tests, but the response trailer can differ.
For faithful driver emulation, pad CDBs to 12 bytes unless a command-specific
test proves the shorter form is required.

Safe live health probes:

```text
c=&i&dl=0
c=000000000000000000000000&i&dl=0
```

The second form is driver-padded `TEST UNIT READY`. Do not use `dl=18` for
`TEST UNIT READY`; a parallel live session observed a 36-byte response with a
non-zero tail for that malformed health probe.

## Canon Local Command Container

**Status:** confirmed-driver.

The macOS TWAIN code builds a local 12-byte Canon container before the CDB. This
container is not sent as part of the `c=` field; `CNetworkScanner` strips it and
sends bytes starting at offset `0x0c`.

Command container:

| Offset | Size | Value / Meaning |
| --- | ---: | --- |
| 0x00 | 4 | `00 00 00 14`, likely local command container length |
| 0x04 | 4 | `00 01 90 00`, local command container magic/tag |
| 0x08 | 4 | `00 00 00 00`, unknown local field |
| 0x0c | n | CDB begins here |

Data-out container:

| Offset | Size | Value / Meaning |
| --- | ---: | --- |
| 0x00 | 4 | Big-endian `payload_len + 8` |
| 0x04 | 4 | `00 02 b0 00`, local data container magic/tag |
| 0x08 | 4 | `00 00 00 00`, unknown local field |
| 0x0c | n | Data-out payload begins here |

The HTTP `d=` field contains only the payload from offset `0x0c`, not this
local data container header.

## cgiscsi Response Body

**Status:** confirmed-wire, confirmed-driver.

The HTTP response body is:

```text
<data-in bytes><18-byte trailer>
```

For status-only commands, the body is exactly the 18-byte trailer.

Trailer layout:

| Trailer offset | Size | Meaning |
| --- | ---: | --- |
| 0x00 | 14 | Saved request-sense-like data |
| 0x0e | 4 | `unknown_trailer_status_or_flags_le32` |

The first 14 trailer bytes are copied by the driver and returned locally if the
next pending command is `REQUEST SENSE` (`0x03`). The driver normally avoids a
real network `REQUEST SENSE` transaction.

The final four bytes are read as a native little-endian 32-bit integer and
stored at object offset `0x24`. `GetResponse` only tests whether this value is
non-zero; if non-zero, it returns `0x100000` in the response word. The exact
field semantics are therefore unknown. Driver-padded live `INQUIRY` responses
ended with:

```text
00 00 00 02
```

which is `0x02000000` as a little-endian integer. `TEST UNIT READY` returned
both this trailer form and an all-zero trailer in different live attempts, so
this field should not be treated as a simple portable SCSI status without more
live traces.

Observed non-zero status-only trailer form:

```text
f0 00 06 00 00 00 00 06 00 00 00 00 00 00 00 00 00 02
```

## Opcode Summary

`CNetworkScanner` recognizes these commands for staged network I/O.

| Opcode | Name | Direction | Status |
| ---: | --- | --- | --- |
| `0x00` | TEST UNIT READY | no data | confirmed-wire |
| `0x03` | REQUEST SENSE | data-in, usually local from trailer | confirmed-driver |
| `0x12` | INQUIRY | data-in | confirmed-wire |
| `0x16` | RESERVE UNIT | no data | confirmed-driver |
| `0x17` | RELEASE UNIT | no data | confirmed-driver |
| `0x1b` | SCAN | data-out | confirmed-driver |
| `0x24` | SET WINDOW | data-out | confirmed-driver |
| `0x25` | GET WINDOW | data-in | confirmed-driver |
| `0x28` | READ | data-in | confirmed-driver |
| `0x2a` | SEND | data-out | confirmed-driver |
| `0x31` | OBJECT POSITION | no data | confirmed-driver |
| `0x3b` | GET MEMORY / READ BUFFER style | data-in | confirmed-driver |
| `0xc5` | GET SCANNER STATUS | data-in | confirmed-driver |
| `0xd6` | DEFINE SCAN MODE / SET SCAN MODE | data-out | confirmed-driver |
| `0xd8` | STOP BATCH / CANCEL | no data | confirmed-driver |
| `0xe1` | SET ADJUST DATA / coarse calibration | data-out | confirmed-driver |

`CNetworkScanner::IsWriteCommand` returns true for `0x1b`, `0x24`, `0x2a`,
`0xd6`, and `0xe1`. `IsReadCommand` returns true for `0x03`, `0x12`, `0x25`,
`0x28`, `0x3b`, and `0xc5`.

## CDB Layouts

Unless otherwise noted, multi-byte SCSI fields in CDBs and scanner payloads are
big-endian. The Canon local wrapper fields above are local implementation
details and are stripped before HTTP.

### 0x00 TEST UNIT READY

**Status:** confirmed-wire.

Standard 6-byte CDB:

```text
00 00 00 00 00 00
```

The live scanner also accepted a 5-byte zero CDB in the form `c=0000000000`.

### 0x03 REQUEST SENSE

**Status:** confirmed-driver.

Standard 6-byte CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x03` |
| 4 | allocation length, Canon uses `0x0e` |

The network plugin usually synthesizes this response from the previous cgiscsi
18-byte trailer instead of sending it over HTTP. A direct live
`REQUEST SENSE` POST timed out during this task.

Sense decoding:

| Sense offset | Meaning |
| --- | --- |
| 2 low nibble | sense key |
| 2 bit 7 | filemark flag |
| 2 bit 5 | information field valid |
| 3..6 | information, big-endian, if valid |
| 12 | ASC |
| 13 | ASCQ |

### 0x12 INQUIRY

**Status:** confirmed-wire.

Standard inquiry:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x12` |
| 1 bit 0 | EVPD |
| 2 | page code |
| 4 | allocation length |

Observed driver forms:

```text
12 00 00 00 40 00        normal INQUIRY, driver asks for 0x40
12 01 f0 00 40 00        Canon capability/VPD page 0xf0
```

Live tested form:

```text
12 00 00 00 60 00        normal INQUIRY, 96-byte allocation
```

The first 36 to 48 bytes match standard scanner INQUIRY. VPD page `0xf0`
matches the SANE `canon_dr` convention for Canon scanner capabilities: basic
resolution, max/min resolution, supported resolution flags, and maximum window
size.

### 0x16 RESERVE UNIT

**Status:** confirmed-driver.

Standard 6-byte CDB with only opcode set:

```text
16 00 00 00 00 00
```

### 0x17 RELEASE UNIT

**Status:** confirmed-driver.

Standard 6-byte CDB with only opcode set:

```text
17 00 00 00 00 00
```

### 0x1b SCAN

**Status:** confirmed-driver.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x1b` |
| 4 | data-out payload length: `1` for simplex/front/back, `2` for duplex |

Data-out payload is a list of window IDs:

| Value | Meaning |
| ---: | --- |
| `0x00` | front window |
| `0x01` | back window |
| `0xf3` | blank-space calibration scan value used by Canon `BlankSpaceScan` |
| `0xfe`, `0xff` | light-adjust calibration scan values used by Canon `AdjustLight` |

SANE-derived DR-C225 calibration notes add another calibration variant:
two-byte `SCAN` payload `ff ff` for offset/lamp-off calibration and `fe fe`
for exposure/lamp-on calibration. The exact clean-room image path validated in
this task did not require those calibration scans, so treat the `0xf3` /
`0xfe` / `0xff` and `ff ff` / `fe fe` forms as calibration-path variants until
more live traces distinguish them.

Examples:

```text
c=1b0000000100&o&d=00&dl=1       start front/simplex scan
c=1b0000000200&o&d=0001&dl=2     start duplex scan
c=1b0000000200000000000000&o&d=ffff&dl=2  SANE-derived offset/lamp-off calibration
c=1b0000000200000000000000&o&d=fefe&dl=2  SANE-derived exposure/lamp-on calibration
```

The Canon driver sends this through its staged write path, so the command is
written first and the data-out payload is written in a second call.

### 0x24 SET WINDOW

**Status:** confirmed-driver.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x24` |
| 6..8 | data-out length, 3-byte big-endian; Canon uses `00 00 34` |

Data-out payload length is 0x34 bytes:

| Payload offset | Size | Meaning |
| --- | ---: | --- |
| 0x00..0x05 | 6 | zero/unknown header bytes |
| 0x06..0x07 | 2 | window descriptor block length, big-endian `0x002c` |
| 0x08..0x33 | 0x2c | one window descriptor |

Window descriptor at payload offset `0x08`:

| Desc offset | Size | Meaning |
| --- | ---: | --- |
| 0x00 | 1 | window id: `0x00` front, `0x01` back |
| 0x01 bit 0 | 1 bit | auto bit |
| 0x02..0x03 | 2 | X resolution, dpi |
| 0x04..0x05 | 2 | Y resolution, dpi |
| 0x06..0x09 | 4 | upper-left X, 1/1200 inch |
| 0x0a..0x0d | 4 | upper-left Y, 1/1200 inch |
| 0x0e..0x11 | 4 | width, 1/1200 inch |
| 0x12..0x15 | 4 | height, 1/1200 inch |
| 0x16 | 1 | brightness |
| 0x17 | 1 | threshold |
| 0x18 | 1 | contrast |
| 0x19 | 1 | image composition |
| 0x1a | 1 | bits per pixel |
| 0x1b | 1 | halftone type |
| 0x1c | 1 | halftone pattern |
| 0x1d | 1 | RIF/RGB/padding bitfields |
| 0x1e..0x1f | 2 | bit ordering |
| 0x20 | 1 | compression type: `0x00` none, `0x80` JPEG |
| 0x21 | 1 | compression argument, JPEG quality-like argument |
| 0x22..0x2b | 10 | unknown/reserved Canon fields |

Canon macOS `ExecSetWindow` builds this payload from an internal `SScanWindow`
and, in the traced network path, forces or copies several fields that differ
from a generic SANE-style payload:

| Desc offset | Canon driver behavior |
| ---: | --- |
| `0x16` | Brightness is written as `0x00` in the traced path. |
| `0x18` | Contrast is written as `0x00` in the traced path. |
| `0x1d` | Driver writes `0x10`. |
| `0x2a` | Driver copies internal byte `SScanWindow[0x1f]`; often zero, but non-zero in some color/sensor modes. |

Composition values from SANE prior art:

| Value | Meaning |
| ---: | --- |
| `0` | lineart |
| `1` | halftone |
| `2` | grayscale |
| `3` | color |
| `4` | color halftone |
| `5` | color grayscale |

For duplex, related SANE code sends SET WINDOW twice: once for front window id
`0x00`, then once for back window id `0x01`. The Canon macOS `ExecSetWindow`
function builds one window descriptor per command call.

### 0x25 GET WINDOW

**Status:** confirmed-driver.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x25` |
| 6..8 | requested data-in length, 3-byte big-endian; Canon sets `00 00 34` |

The response payload uses the same 0x34-byte window payload layout as
SET WINDOW.

### 0x28 READ

**Status:** confirmed-driver.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x28` |
| 2 | data type |
| 4 | UID or high selector byte |
| 5 | LID or low selector byte |
| 6..8 | transfer length, 3-byte big-endian |

Common data type values from SANE and the Canon driver's lookup table:

| Data type | Meaning |
| ---: | --- |
| `0x00` | image data |
| `0x80` | pixel size / page geometry variant |
| `0x84` | panel |
| `0x8b` | sensors |
| `0x8c` | counters |
| `0x91` | fine calibration gain/offset variants |
| `0xa1` | unknown Canon status/data page |
| `0xaa` | unknown Canon data page |

The macOS `ExecRead` wrapper accepts an internal read-kind enum and maps it
through two constant tables before sending `0x28`. The table below gives the
resulting wire fields; lengths are supplied separately by the caller.

| Driver read kind | CDB byte 2 | CDB bytes 4..5 | Observed use |
| ---: | ---: | --- | --- |
| `0` | `0x00` | `00 00` | image data |
| `1` | `0x80` | `00 00` | geometry/pixel-size variant |
| `2` | `0x80` | `00 04` | geometry/pixel-size variant |
| `3` | `0x80` | `00 01` | geometry/pixel-size variant |
| `4` | `0x84` | `00 00` | panel/button state |
| `5` | `0x8b` | `00 00` | sensors |
| `6` | `0x8c` | `00 00` | 0x80-byte pre-scan/device block used by `StartScan` |
| `7` | `0x8c` | `00 01` | alternate counter/state block |
| `8` | `0xa1` | `00 00` | unknown Canon page |
| `9` | `0x91` | `07 00` | unknown Canon page/selector |
| `10` | `0x91` | `09 00` | unknown Canon page/selector |
| `11` | `0x91` | `0a 00` | unknown Canon page/selector |
| `12` | `0x91` | `0c 00` | unknown Canon page/selector |
| `13` | `0x91` | `23 00` | unknown Canon page/selector |
| `14` | `0x91` | `25 00` | unknown Canon page/selector |
| `15` | `0x91` | `26 00` | unknown Canon page/selector |
| `16` | `0x00` | `00 00` | image/default table entry |
| `17` | `0xaa` | `00 00` | unknown Canon page |

For image reads, use:

```text
28 00 00 00 00 00 LL LL LL 00
```

where `LL LL LL` is the requested chunk length. The driver clips reads to line
boundaries and, for JPEG, patches the JPEG SOF dimensions after receiving image
data because some Canon streams omit or zero them.

SANE-derived DR-C225 calibration notes include this 300 dpi, 8-line, duplex RGB
calibration READ:

```text
c=28000000000001de20000000&i&dl=122400
```

This is not part of the live-validated JPEG path in this repo; it is retained
as an executable clue for future calibration work.

### 0x2a SEND

**Status:** confirmed-driver.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x2a` |
| 2 | data type |
| 4..5 | data id / selector |
| 6..8 | data-out length, 3-byte big-endian |

The decoded macOS wrapper maps some internal send kinds to:

| Internal kind | CDB data type | CDB id bytes | Meaning |
| ---: | ---: | --- | --- |
| `6` | `0x8c` | `00 00` | likely counter/panel-related, inferred |
| `7` | `0x8c` | `00 01` | likely counter/panel-related, inferred |
| `16` | `0x90` | `00 00` | fine calibration / endorser-related, inferred |

SANE-derived fine-calibration request examples use data type `0x90` for offset
and `0x91` for gain, each with a 5104-byte (`0x13f0`) data-out payload:

```text
c=2a00900000000013f0000000&o&d=...&dl=5104
c=2a00910000000013f0000000&o&d=...&dl=5104
```

Exact high-level use of these variants was not fully traced for the DR-C225W II.

### 0x31 OBJECT POSITION

**Status:** confirmed-driver.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x31` |
| 1 | Canon action byte |

Action values:

| Driver action argument | CDB byte 1 | Meaning |
| ---: | ---: | --- |
| `0` | `0x00` | discharge/eject |
| `1` | `0x01` | feed/load |
| `2` | `0x04` | reposition/recovery path, exact public meaning unknown |

The macOS `ExecObjectPosition(action)` implementation accepts actions `0`,
`1`, and `2`, then maps them through a switch to CDB byte values `00`, `01`,
and `04`. `StartScan` can call action `2` when previous device state indicates
that a reposition/clear-path step is needed; normal observed live control only
covered actions `0` and `1`.

Examples:

```text
31 01 00 00 00 00 00 00 00 00    load/feed next sheet
31 00 00 00 00 00 00 00 00 00    discharge/eject
31 04 00 00 00 00 00 00 00 00    reposition/recovery action, inferred
```

### 0x3b GET MEMORY

**Status:** confirmed-driver.

The Canon macOS function is named `ExecGetMemory(offset, length, out)`.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0x3b` |
| 2..5 | memory offset/address, big-endian |
| 7..8 | transfer length, big-endian 16-bit |

The driver reads in chunks up to `0x2000` bytes until the requested total length
is satisfied.

### 0xc5 GET SCANNER STATUS

**Status:** confirmed-driver.

CDB as built by Canon:

```text
c5 00 00 00 08 00 00 00 00 00 00 00
```

Expected data-in length: 8 bytes.

The driver reports "busy/status set" if returned byte 0 has bit `0x40` set or
returned byte 1 is non-zero. Other returned fields are unknown.

### 0xd6 DEFINE SCAN MODE

**Status:** confirmed-driver.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0xd6` |
| 1 | `0x10` in Canon macOS driver |
| 4 | data-out length, Canon uses `0x14` |

Data-out payload length is 0x14 bytes. The driver supports three internal
parameter modes:

| Internal mode | Payload page code | Observed use |
| ---: | ---: | --- |
| `0` | `0x30` | double-feed / document-feed controls |
| `1` | `0x32` | buffer/source controls |
| `2` | `0x36` | dropout/color handling controls |

Payload offset `0x04` is the page code and offset `0x05` is the page length
`0x0e`, matching SANE `SET_SCAN_MODE` prior art.

Decoded page byte mappings from `ExecDefineScanMode`:

| Page | Payload offset | Source parameter | Notes |
| ---: | ---: | --- | --- |
| `0x30` | `0x07` | mode-0 byte 4/5 combination | If source byte 4 is set, byte `0x07` starts as `0x01`; if source byte 5 is also set it becomes `0x05`. If only source byte 5 is set it becomes `0x04`. |
| `0x30` | `0x09` | mode-0 byte 6 | Set to `0x10` when source byte 6 is non-zero. |
| `0x32` | `0x06` | mode-1 byte 4 | Driver copies the byte directly. |
| `0x32` | `0x07` | constant | Driver sets `0x01`. |
| `0x32` | `0x0a` | mode-1 bytes 5, 6, 10 | Bits `0x40`, `0x20`, and `0x08` respectively. |
| `0x32` | `0x0c..0x0d` | mode-1 word at byte 8 | Stored big-endian. |
| `0x36` | `0x0b..0x0e` | mode-2 bytes 4..7 | Driver copies the bytes directly. |
| `0x36` | `0x11..0x12` | mode-2 bytes 8..9 | Driver copies the bytes directly. |

The exact user-facing meaning of every flag remains partially inferred, but the
page construction and offsets above are from the macOS binary.

### 0xd8 STOP BATCH / CANCEL

**Status:** confirmed-driver.

Standard 6-byte vendor command with only opcode set:

```text
d8 00 00 00 00 00
```

The Canon function is named `ExecStopBatch`.

### 0xe1 SET ADJUST DATA

**Status:** confirmed-driver.

The Canon function is named `ExecSetAdjustData`. It sends opcode `0xe1` and a
0x28-byte data-out payload for the version-3 coarse calibration form. The local
data container length is 0x30 bytes including the stripped 8-byte local data
header.

CDB:

| CDB offset | Meaning |
| --- | --- |
| 0 | `0xe1` |
| 5 | calibration payload version; Canon uses `0x03` |
| 6..8 | data-out length, 3-byte big-endian; Canon uses `00 00 28` |

The 0x28-byte version-3 payload matches the SANE `CC3` coarse-calibration
layout and the field copies in Canon `ExecSetAdjustData`:

| Payload offset | Size | Meaning |
| --- | ---: | --- |
| 0x00 | 1 | front red gain |
| 0x01 | 1 | front green gain |
| 0x02 | 1 | front blue gain |
| 0x04 | 1 | front red offset |
| 0x05 | 1 | front green offset |
| 0x06 | 1 | front blue offset |
| 0x08..0x09 | 2 | front red exposure, big-endian |
| 0x0a..0x0b | 2 | front green exposure, big-endian |
| 0x0c..0x0d | 2 | front blue exposure, big-endian |
| 0x14 | 1 | back red gain |
| 0x15 | 1 | back green gain |
| 0x16 | 1 | back blue gain |
| 0x18 | 1 | back red offset |
| 0x19 | 1 | back green offset |
| 0x1a | 1 | back blue offset |
| 0x1c..0x1d | 2 | back red exposure, big-endian |
| 0x1e..0x1f | 2 | back green exposure, big-endian |
| 0x20..0x21 | 2 | back blue exposure, big-endian |

Canon calls this during light/blank-space calibration before the user-visible
scan path, depending on cached calibration state and selected image mode.

## Cross-Reference to SANE canon_dr

| Command | Canon DR-C225W II driver | SANE `canon_dr` prior art | Notes |
| --- | --- | --- | --- |
| TEST UNIT READY | `0x00`, no data | same opcode and 6-byte CDB | match |
| REQUEST SENSE | `0x03`, 14 bytes, often local from trailer | same sense layout | network wrapper caches trailer |
| INQUIRY | `0x12`, std and EVPD page `0xf0` | same standard and Canon VPD page | match |
| RESERVE/RELEASE | `0x16`/`0x17` | standard SCSI | match |
| SCAN | `0x1b`, payload window ids | SANE uses window ids front/back | match |
| SET WINDOW | `0x24`, 0x34 payload | SANE header 8 + descriptor 0x2c | match |
| GET WINDOW | `0x25`, 0x34 response | related scanner command | Canon driver gives clearer length |
| READ | `0x28`, data type at byte 2, length bytes 6..8 | same | match |
| SEND | `0x2a`, data type at byte 2, length bytes 6..8 | same | match |
| OBJECT POSITION | `0x31` | same opcode; driver maps action 0/1/2 to byte 1 values 00/01/04 | match |
| SET SCAN MODE | `0xd6`, 0x14 payload | same opcode and page structure | match for page framing |
| STOP BATCH/CANCEL | `0xd8` | SANE cancel opcode | match |
| SET ADJUST DATA | `0xe1`, version 3, 0x28-byte CC3 payload | SANE coarse calibration opcode and CC3 layout | match |
| GET SCANNER STATUS | `0xc5` | not clearly covered by SANE DR source | Canon vendor-specific |
| GET MEMORY | `0x3b` | not part of core scan workflow in SANE | Canon support/diagnostic path |

## Workflow Sequences

These sequences are suitable starting points for a clean-room implementation.
The envelope and command layouts above are stronger evidence than the exact
workflow order below, which combines Canon driver function names with SANE
`canon_dr` scan flow.

### Discovery / Identification

1. Start from a configured IP address or external discovery result.
2. Optionally `GET /` or TCP-connect to confirm the embedded HTTP server.
3. Send `TEST UNIT READY`.
4. Send normal `INQUIRY`.
5. Send EVPD `INQUIRY` page `0xf0` and parse scanner capabilities.

### Simplex ADF JPEG Scan

1. `TEST UNIT READY`.
2. `INQUIRY`, optionally VPD page `0xf0`.
3. `RESERVE UNIT`.
4. `OBJECT POSITION feed` (`0x31`, action `1`) if the ADF workflow requires
   loading a sheet.
   The decoded action `2` maps to CDB byte `0x04` and appears to be a
   reposition/recovery step, not part of the normal happy-path feed/discharge
   cycle.
5. Read driver kind `6` (`0x28`, type `0x8c`, selector `00 00`, length
   `0x80`) as the macOS `StartScan` path does before window setup.
6. Run Canon's light/blank-space calibration path if cached calibration is not
   valid for the current resolution, color mode, side count, and sensor state.
   This path can issue its own calibration `SET WINDOW`, `DEFINE SCAN MODE`,
   `SCAN`, `READ`, `OBJECT POSITION`, and `SET ADJUST DATA (0xe1)` commands
   before the final user-visible scan.
7. `SET WINDOW` for front window id `0x00`, with compression type `0x80` for
   JPEG or `0x00` for raw.
8. Send `DEFINE SCAN MODE` page `0x30`, then `0x32`, then `0x36`.
9. `SCAN` with payload `00`.
10. Loop `READ` image data (`0x28`, data type `0x00`) in chunks until the trailer
   sense data or higher-level length accounting indicates EOF.
   Treat sense key `0x05` with ASC `0x3a` or `0x2c` after image reads as no more
   image data for the current scan sequence; live 6-sheet testing showed these
   values after the current duplex sheet was exhausted.
11. For JPEG output, preserve the JPEG stream and patch SOF dimensions if needed.
12. `OBJECT POSITION discharge` when the page is complete.
13. `RELEASE UNIT`.

### Duplex Multi-Page ADF Scan

1. Perform the same setup as simplex.
2. Send `SET WINDOW` for front window id `0x00`.
3. Send `SET WINDOW` for back window id `0x01`.
4. Configure duplex/source bits in `DEFINE SCAN MODE` pages, especially page
   `0x32`, if needed.
5. Send `SCAN` with payload `00 01`.
6. Loop `READ` image data until two JPEG frames are complete for the current
   duplex sheet.
7. End that sheet with `STOP BATCH`/`CANCEL`, `OBJECT POSITION discharge`, and
   `RELEASE UNIT`.
8. Repeat the same per-sheet sequence for each remaining ADF sheet. Live testing
   showed one `SCAN` consumes one duplex sheet; asking a single `SCAN` for more
   than the current sheet produced two JPEGs followed by `05/3a` and repeated
   `05/2c` sense values.
9. For unknown simplex/duplex input, scan every sheet in duplex mode, then drop
   pages that are classified as blank before final PDF assembly. The harness
   keeps the per-sheet raw streams and original JPEGs under each `sheet-*`
   directory even when a blank page is omitted from the final PDF. For A4 input
   it uses a 1200-unit window of `0x26c0 x 0x36d0`, yielding about
   `2480 x 3508` pixels at 300 dpi; this avoids the earlier Letter-sized
   `2550 x 3300` window that cropped A4 pages.
10. Convert each kept front/back JPEG or raw image to a PDF page in capture
    order. The current harness rotates kept pages 180 degrees by default before
    final PDF assembly to correct the observed feed orientation.

## PDF Assembly Notes

The protocol returns image bytes, not PDF. For JPEG mode, each completed JPEG
frame can be placed directly into a PDF page. For raw mode, the implementation
must know width, height, color mode, bits per pixel, and stride from SET WINDOW
and/or a pixel-size READ response, then wrap the raster in an image object before
PDF assembly.

The harness includes a Pillow-based JPEG-to-PDF helper. It intentionally does
not launch a scan by default. Its guarded `--experimental-scan` path can capture
the raw image stream, extract complete JPEG frames delimited by `ff d8` / `ff
d9`, and assemble those frames into a PDF. For duplex, the default guarded
capture stop condition is two complete JPEG frames; higher frame counts can be
requested for multi-page ADF tests. The same path can document raw capture with
`--image-format raw`, which sets the SET WINDOW compression byte to `0x00`; when
raw width/height/mode are supplied, it wraps the captured stream into a PDF.
That capture path is protocol documentation in executable form until the
scan sequence is further generalized beyond the live-validated JPEG path.

For raw output, `scan_to_pdf.py --raw-to-pdf` can wrap packed 1-bit, 8-bit
grayscale, or 24-bit RGB raster data into a PDF when width, height, and optional
row stride are supplied from SET WINDOW and/or pixel-size reads.

## Validation Record

Live driver-padded `TEST UNIT READY`:

```text
Request body: c=000000000000000000000000&i&dl=0
HTTP:         200 OK
Body length: 18
Trailer A:   00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
Trailer B:   f0 00 06 00 00 00 00 06 00 00 00 00 00 00 00 00 00 02
```

Live empty probe:

```text
Request body: c=&i&dl=0
HTTP:         200 OK
Body length: 18
Trailer:      00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

Live driver-padded `INQUIRY`:

```text
Request body: c=120000006000000000000000&i&dl=96
HTTP:         200 OK
Body length: 114
Data-in:      96 bytes
Trailer:      18 bytes
Device:       CANON DR-C225 rev 1.06
```

An unpadded `INQUIRY` retry timed out after 5 seconds during earlier
experiments. This may indicate a single-request/blocking scanner state or
CDB-length sensitivity, so the harness should avoid concurrent requests, prefer
driver-padded CDBs, and use conservative timeouts.

Live EVPD `INQUIRY` page `0xf0` with padded CDB
`12 01 f0 00 40 00 00 00 00 00 00 00` also timed out during this task. The
page layout remains documented from Canon driver construction and SANE prior
art, not from a successful DR-C225W II live response.

Additional live control validation:

```text
GET SCANNER STATUS: c=c50000000800000000000000&i&dl=8
Response data:      40 00 00 00 00 00 00 00

READ panel:         c=280084000000000008000000&i&dl=8
Response data:      80 00 00 01 00 00 00 00

SET WINDOW + GET WINDOW:
SET WINDOW accepted a 0x34-byte 150 dpi grayscale/JPEG front-window payload.
GET WINDOW returned the same 0x34-byte payload.

OBJECT POSITION:
Both feed (`31 01 ...`) and discharge (`31 00 ...`) returned HTTP 200 status
responses.
```

Live scan-start caveat:

```text
SCAN attempts after conservative SET WINDOW / DEFINE SCAN MODE setup returned
sense-like trailer bytes consistent with key 0x05, ASC 0x26 (invalid field in
parameter list). A malformed no-data SCAN probe later left the scanner's
cgiscsi CGI timing out while the embedded HTTP server still answered GET /.
```

After correcting SET WINDOW defaults and recovering the scanner physically, the
duplex JPEG scan path succeeded:

```text
Command:
uv run python scan_to_pdf.py --host <scanner-ip> --duplex --execute-plan \
  --experimental-scan --timeout 30 --output-dir captures/live-duplex \
  --output-pdf captures/live-duplex/scan.pdf --max-chunks 8 \
  --stop-after-frames 2

Result:
SCAN returned success sense.
Five 64 KiB image READ chunks were enough to contain two complete JPEG frames.
page-001.jpg: 2550x3300 grayscale JPEG, 300 dpi
page-002.jpg: 2550x3300 grayscale JPEG, 300 dpi
scan.pdf:     PDF 1.4, 2 pages
```

A second run with `--stop-after-frames 4` produced another two-page PDF and
then reported `05/3a` followed by repeated `05/2c`, consistent with no further
sheet/image data after the available duplex page.

With six A4 sheets in the tray, repeating the verified duplex sheet sequence
six times produced 12 ordered JPEG pages and a 12-page PDF:

```text
harness/captures/e2e-20260509-192542/pages/page-001.jpg .. page-012.jpg
harness/captures/e2e-20260509-192542/scan-12pages.pdf
file: PDF document, version 1.4, 12 pages
```

Therefore, the HTTP envelope, response framing, discovery, status, ADF
mechanical control, window exchange, SCAN start, image READ, JPEG extraction,
multi-sheet duplex JPEG capture, and PDF assembly are live-confirmed. Remaining
weaker areas are raw image capture against the live device and any unknown
firmware mode that might auto-feed multiple sheets under one `SCAN`.

Unknown-count, unknown-sidedness live runs used the harness `--scan-all` mode.
The final validation scanned duplex sheet-by-sheet, stopped when the seventh
sheet attempt returned no image data, dropped blank backs, and assembled:

```text
harness/captures/auto-e2e-20260509-200013/scan.pdf
file: PDF document, version 1.4, 6 pages
```

If `cgiscsi` enters a state where POSTs time out but the embedded web server
still responds, the scanner web UI exposes a restart endpoint observed by a
parallel session:

```text
http://<scanner-ip>/eng/private/mainte/restart_main.htm
```
