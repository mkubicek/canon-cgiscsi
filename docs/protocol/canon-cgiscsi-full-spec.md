# Canon cgiscsi Full Protocol Spec

This is the consolidated clean-room protocol specification for the Canon
imageFORMULA DR-C225W / DR-C225W II network scanner interface exposed at
`POST /cgi-bin/cgiscsi`. It reconciles `protocol-spec.md`, `harness/`, and the
driver evidence log into an implementation-oriented reference for an AirScan
adapter backend.

## Confidence Labels

| Label | Meaning |
| --- | --- |
| confirmed-wire | Observed against the tested scanner and captured in repo notes/logs. |
| confirmed-driver | Decoded from Canon driver metadata/symbols/control-flow summaries. |
| confirmed-repo | Implemented or tested in this repo's independent harness. |
| confirmed-SANE | Public SANE `canon_dr` prior art. |
| inferred | Strongly suggested by multiple sources but not directly validated. |
| unknown | Present field or behavior with unknown semantics. |

## Transport

| Field | Value | Confidence | Evidence |
| --- | --- | --- | --- |
| HTTP method | `POST` | confirmed-wire, confirmed-driver | `protocol-spec.md:90`, `notes/function-summaries/url-builder.md` |
| Path | `/cgi-bin/cgiscsi` | confirmed-wire, confirmed-driver | `protocol-spec.md:96`, `harness/cgiscsi.py:52` |
| Content type | `application/x-www-form-urlencoded` | confirmed-driver | `protocol-spec.md:109`, `harness/cgiscsi.py:127` |
| Request body encoding | ASCII form text with lowercase hex CDB/data bytes | confirmed-wire, confirmed-driver | `protocol-spec.md:121`, `harness/cgiscsi.py:96` |
| Response body | Data-in bytes followed by 18-byte trailer | confirmed-wire, confirmed-driver | `protocol-spec.md:195`, `harness/cgiscsi.py:83` |
| CDB length | Prefer 12-byte padded CDBs | confirmed-driver, confirmed-wire | `protocol-spec.md:149`, `harness/cgiscsi.py:122` |
| Single active command | Required by safety; concurrent probes can wedge or confuse state | inferred, confirmed-wire for sensitivity | `protocol-spec.md:895` |

### Request Bodies

```text
c=<hex-cdb>&i&dl=<expected-data-in-length>
c=<hex-cdb>&o&d=<hex-data-out>&dl=<data-out-length>
```

The optional `a=<client-mac>` parameter exists in the driver but is not required
for the confirmed harness path.

### Response Trailer

| Offset | Size | Meaning | Confidence |
| ---: | ---: | --- | --- |
| `0x00` | 14 | Sense-like data cached by Canon driver for local `REQUEST SENSE`. | confirmed-driver, confirmed-wire |
| `0x0e` | 4 | Little-endian unknown status/flags. Driver only checks nonzero. | confirmed-driver, unknown |

Do not decode the final four bytes as portable SCSI status until more traces
exist. Live successful requests have returned both zero and nonzero forms.

## Command Catalog

| Opcode | Name | Direction | CDB length | Payload/data length | Confidence | Notes |
| ---: | --- | --- | ---: | --- | --- | --- |
| `0x00` | TEST UNIT READY | none | 6, padded to 12 | 0 | confirmed-wire | Safe health probe if `dl=0`. |
| `0x03` | REQUEST SENSE | data-in | 6, padded to 12 | 14 | confirmed-driver | Usually synthesized from prior trailer; direct live request timed out. |
| `0x12` | INQUIRY | data-in | 6, padded to 12 | 0x40 or 0x60 | confirmed-wire | Standard identity validated; VPD `0xf0` timed out live. |
| `0x16` | RESERVE UNIT | none | 6, padded to 12 | 0 | confirmed-wire | Use per job. |
| `0x17` | RELEASE UNIT | none | 6, padded to 12 | 0 | confirmed-wire | Always attempt in cleanup after reserve. |
| `0x1b` | SCAN | data-out | 6, padded to 12 | 1 or 2 bytes | confirmed-wire for normal scan | Payload is window IDs. |
| `0x24` | SET WINDOW | data-out | 10, padded to 12 | 0x34 | confirmed-wire | One window descriptor per command. |
| `0x25` | GET WINDOW | data-in | 10, padded to 12 | 0x34 | confirmed-wire | Mirrors SET WINDOW payload. |
| `0x28` | READ | data-in | 10, padded to 12 | caller chunk length | confirmed-wire | Image reads use type `0x00`. |
| `0x2a` | SEND | data-out | 10, padded to 12 | caller payload length | confirmed-driver | Calibration/fine data paths; not v1 adapter default. |
| `0x31` | OBJECT POSITION | none | 10 | 0 | confirmed-wire for `00`/`01` | Feed/discharge; action `04` is recovery/unknown. |
| `0x3b` | GET MEMORY | data-in | 10 | max 0x2000 chunks | confirmed-driver | Diagnostic/support path, not v1 adapter default. |
| `0xc5` | GET SCANNER STATUS | data-in | 12 | 8 | confirmed-wire | Canon vendor status. |
| `0xd6` | DEFINE SCAN MODE | data-out | 6, padded to 12 | 0x14 | confirmed-driver | Pages `0x30`, `0x32`, `0x36`. |
| `0xd8` | STOP BATCH / CANCEL | none | 6, padded to 12 | 0 | confirmed-driver | Use for eSCL DELETE/cancel. |
| `0xe1` | SET ADJUST DATA / COR_CAL | data-out | 10, padded to 12 | 0x28 | confirmed-driver, confirmed-SANE | Calibration only; do not activate by default. |

## CDB and Payload Details

### TEST UNIT READY `0x00`

```text
00 00 00 00 00 00
```

Use `dl=0`. Do not use `dl=18` as a health probe.

### REQUEST SENSE `0x03`

| CDB offset | Meaning |
| ---: | --- |
| 0 | `0x03` |
| 4 | allocation length, Canon uses `0x0e` |

Sense decoding:

| Sense byte | Meaning |
| ---: | --- |
| 2 low nibble | sense key |
| 12 | ASC |
| 13 | ASCQ |

### INQUIRY `0x12`

| CDB offset | Meaning |
| ---: | --- |
| 0 | `0x12` |
| 1 bit 0 | EVPD |
| 2 | page code |
| 4 | allocation length |

Known forms:

| Form | Confidence | Notes |
| --- | --- | --- |
| `12 00 00 00 60 00` padded to 12 | confirmed-wire | Returns standard identity. |
| `12 01 f0 00 40 00` padded to 12 | confirmed-driver, confirmed-SANE | Canon VPD/capability page, not live-successful in this repo. |

### SCAN `0x1b`

| CDB offset | Meaning |
| ---: | --- |
| 0 | `0x1b` |
| 4 | data-out length: `1` simplex/back-only, `2` duplex |

| Payload byte | Meaning | Confidence |
| ---: | --- | --- |
| `0x00` | front window | confirmed-driver, confirmed-wire |
| `0x01` | back window | confirmed-driver, confirmed-wire |
| `0xf3` | blank-space calibration scan marker | confirmed-driver |
| `0xfe`, `0xff` | light-adjust calibration markers | confirmed-driver, confirmed-SANE |

Validated forms:

```text
front:  c=1b0000000100... &o&d=00&dl=1
duplex: c=1b0000000200... &o&d=0001&dl=2
```

### SET WINDOW `0x24`

Payload length is 0x34 bytes. Bytes 6..7 contain block length `0x002c`;
descriptor starts at offset 8.

| Descriptor offset | Size | Meaning | v1 adapter value |
| ---: | ---: | --- | --- |
| `0x00` | 1 | window id | `0x00` front, `0x01` back |
| `0x02..0x03` | 2 | X DPI | `300` default |
| `0x04..0x05` | 2 | Y DPI | `300` default |
| `0x06..0x09` | 4 | upper-left X, 1/1200 inch | `0` |
| `0x0a..0x0d` | 4 | upper-left Y, 1/1200 inch | `0` |
| `0x0e..0x11` | 4 | width, 1/1200 inch | paper preset |
| `0x12..0x15` | 4 | height, 1/1200 inch | paper preset |
| `0x16` | 1 | brightness | `0` |
| `0x17` | 1 | threshold | `0` |
| `0x18` | 1 | contrast | `0` |
| `0x19` | 1 | composition | `2` grayscale |
| `0x1a` | 1 | bits per pixel | `8` |
| `0x1d` | 1 | Canon padding/RIF field | `0x10` |
| `0x20` | 1 | compression | `0x80` JPEG |
| `0x21` | 1 | compression argument | `3` |
| `0x2a` | 1 | vendor byte copied by Canon driver | `0` for default path |

Paper presets from `harness/scan_to_pdf.py:34`:

| Paper | Width units | Height units | 300 DPI pixels |
| --- | ---: | ---: | --- |
| A4 | `2480 * 1200 / 300` | `3508 * 1200 / 300` | about 2480 x 3508 |
| Letter | `2550 * 1200 / 300` | `3300 * 1200 / 300` | 2550 x 3300 |
| Legal | `2550 * 1200 / 300` | `4200 * 1200 / 300` | 2550 x 4200 |

### READ `0x28`

| CDB offset | Meaning |
| ---: | --- |
| 0 | `0x28` |
| 2 | data type |
| 4..5 | UID/LID selector |
| 6..8 | 24-bit transfer length |

| Data type | Selector | Use | Confidence |
| ---: | --- | --- | --- |
| `0x00` | `00 00` | image data | confirmed-wire |
| `0x80` | variants | geometry/pixel-size | confirmed-driver |
| `0x84` | `00 00` | panel | confirmed-wire |
| `0x8b` | `00 00` | sensors | confirmed-wire |
| `0x8c` | `00 00` | counters/pre-scan block | confirmed-wire |
| `0x91` | variants | fine calibration pages | confirmed-driver |
| `0xa1`, `0xaa` | `00 00` | unknown Canon pages | confirmed-driver, unknown |

Image reads should be sequential and conservative. The current harness reads
chunks, appends data, extracts complete JPEG frames by SOI/EOI markers, and
stops on frame count, short read, max bytes, or no-more-image-data sense.

### OBJECT POSITION `0x31`

| Action argument | CDB byte 1 | Meaning | Confidence |
| ---: | ---: | --- | --- |
| 0 | `0x00` | discharge/eject | confirmed-wire |
| 1 | `0x01` | feed/load | confirmed-wire |
| 2 | `0x04` | reposition/recovery | confirmed-driver, unknown |

For the AirScan adapter, action `1` and `0` are paper-motion commands. Default
tests must use mocks. Live use requires explicit user configuration and a scan
job, not health checks.

### DEFINE SCAN MODE `0xd6`

| Page | Meaning | v1 default |
| ---: | --- | --- |
| `0x30` | feed/document flags | zero page unless a validated flag is needed |
| `0x32` | buffer/source flags | byte 6 set to `0x02` for duplex in current harness |
| `0x36` | dropout/color handling flags | zero page for grayscale default |

Payload length is 0x14. Page code is at payload offset 4 and page length
`0x0e` at offset 5. Use `harness/commands.py` builders rather than hand-coded
payload strings.

### CANCEL `0xd8`

Use as the first cleanup step for eSCL DELETE or scan error. Follow with
discharge if a feed occurred, and release if reserve occurred.

### SET ADJUST DATA `0xe1`

This is a calibration/coarse adjustment command. The adapter must not send it in
the default AirScan path. Future calibration work must be an explicit opt-in
with live safety review.

## Validated Scan Lifecycle

```text
single active eSCL job
  reserve
  object_position(feed)
  read_kind(6, 0x80)       # pre-scan block
  set_window(front)
  set_window(back)         # duplex only
  define_scan_mode(0x30)
  define_scan_mode(0x32)
  define_scan_mode(0x36)
  scan([0x00, 0x01])       # duplex default
  read image chunks until two JPEG frames or no-more-image sense
  cancel
  object_position(discharge)
  release
repeat per sheet until no frames, max sheets, cancel, or error
```

Important DR-C225W II behavior:

| Behavior | Confidence | Adapter implication |
| --- | --- | --- |
| One duplex `SCAN` produced one front/back sheet, not an entire ADF stack. | confirmed-wire | Adapter should loop sheets internally and spool pages for `NextDocument`. |
| Duplex JPEG frames arrived as complete JPEGs in page order for validated grayscale mode. | confirmed-wire | No SANE deinterlacing in v1 JPEG path. |
| Blank backs are best handled after capture by dark-pixel fraction. | confirmed-repo, confirmed-wire | Expose blank-back skip as default policy and optional eSCL blank-removal support. |
| A4 window avoids cropping on European office documents. | confirmed-wire | Default A4; allow Letter and Legal. |
| Rotation is best applied as PDF metadata for compact PDFs. | confirmed-repo | eSCL JPEG pages may be delivered as captured; adapter-side OCR PDF can rotate metadata. |
| Malformed scan probes can wedge the CGI while the web server stays up. | confirmed-wire | Keep strict parameter validation, single active job, cleanup, and recovery docs. |

## Status, Sense, and Recovery

| Condition | Evidence | eSCL mapping |
| --- | --- | --- |
| Healthy idle | `TEST UNIT READY` returns HTTP 200/trailer; status page often begins `0x40`. | `pwg:State=Idle`, ADF state from cached/last known backend status if available. |
| No more image data / ADF empty after read | Sense key `0x05`, ASC `0x3a` or `0x2c` after image reads. | End current sheet/job; if no pages captured for attempted sheet, mark ADF empty. |
| Invalid parameter | Sense key `0x05`, ASC `0x26`. | Fail job; tell user adapter sent unsupported settings and advise retry with defaults. |
| Scanner busy/reserved | Canon status bit or failed reserve. | `Processing` or eSCL job error `ServerErrorTemporaryError`; reject concurrent job with 409 or 503. |
| CGI timeout while web server responds | Observed after malformed no-data `SCAN`. | Mark backend `cgiscsi_wedged`; admin page should instruct power-cycle or scanner web restart. |
| Network unreachable | HTTP connect/timeout failure. | `ScannerDown` or HTTP 503 on eSCL requests; admin page shows host and last error. |

Recovery policy:

1. Stop accepting new jobs when backend is wedged or unreachable.
2. Try only non-paper-motion health checks in automatic recovery.
3. Never send `SCAN`, `OBJECT POSITION`, `SEND`, `COR_CAL`, or calibration as a health check.
4. Surface explicit user instructions: clear paper path, press Stop, power-cycle, or use the scanner web restart page if configured.

## Canon-to-Adapter Setting Map

| eSCL setting | Canon mapping | v1 support |
| --- | --- | --- |
| Input source `Feeder` | ADF sequence with `OBJECT POSITION feed`, `SCAN`, image reads, discharge. | yes |
| Duplex `true` | SET WINDOW front/back, scan payload `00 01`, buffer/source page duplex setting. | yes, default |
| Duplex `false` | SET WINDOW front only, scan payload `00`. | yes after testing with mock; live validation later |
| X/Y resolution 300 | SET WINDOW DPI fields. | yes |
| Resolution 200/600 | SET WINDOW fields likely work but not live-confirmed for final UX. | advertise later |
| Grayscale8 | composition `2`, 8 bpp, JPEG compression. | yes, default |
| RGB24 | composition `3`, 24 bpp, JPEG compression. | defer until live validated |
| BlackAndWhite1 | composition `0`, 1 bpp likely; not validated. | do not advertise v1 |
| A4/Letter/Legal | SET WINDOW width/height in 1/1200 inch. | yes |
| JPEG document format | Preserve scanner JPEG frames. | yes |
| PDF document format | Assemble via img2pdf/OCR pipeline. | not default eSCL transport |
| Blank page removal | Post-capture dark-pixel filtering. | yes for adapter-side inbox; eSCL capability can be advertised once behavior is stable |
| Brightness/contrast/gamma/etc. | SET WINDOW or image-processing fields. | do not advertise v1 |
| Calibration | `0xe1`, calibration SCAN/SEND/READ paths. | do not advertise or run v1 |

