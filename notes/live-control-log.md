# Live Control Log

Target scanner: Canon DR-C225W II at `<scanner-ip>`, serial `<serial-redacted>`.

## Verified Commands

These commands were issued through `harness/cgiscsi.py` or equivalent one-off
Python using the same `CgiscsiClient` and CDB builders.

| Command | Result |
| --- | --- |
| Empty probe (`c=&i&dl=0`) | HTTP 200 with an 18-byte all-zero trailer when healthy. |
| `TEST UNIT READY` (`0x00`) | HTTP 200 with 18-byte trailer. Observed both all-zero trailer and non-zero request-sense-like trailer. |
| `INQUIRY` (`0x12`) | HTTP 200, 96-byte data-in, identified `CANON DR-C225 rev 1.06`. |
| `GET SCANNER STATUS` (`0xc5`) | HTTP 200, 8-byte data-in, commonly `40 00 00 00 00 00 00 00`. |
| `RESERVE UNIT` / `RELEASE UNIT` (`0x16` / `0x17`) | HTTP 200 status-only responses. |
| `OBJECT POSITION feed/discharge` (`0x31`) | HTTP 200 status-only responses; mechanical feed/discharge control is reachable. |
| `SET WINDOW` / `GET WINDOW` (`0x24` / `0x25`) | HTTP 200; `GET WINDOW` returned the 0x34-byte window payload that had just been set. |
| `READ` sensors (`0x28`, type `0x8b`, len 1) | HTTP 200, 1-byte data-in. |
| `READ` panel (`0x28`, type `0x84`, len 8) | HTTP 200, data `80 00 00 01 00 00 00 00`. |
| `READ` counters (`0x28`, type `0x8c`, len 0x80) | HTTP 200, non-zero counter data. |

Offline decoding later confirmed this `0x8c`/0x80 read is driver read-kind `6`,
the same pre-window block used by the macOS `StartScan` path.

## Scan Attempts

Conservative ADF setup attempts used `RESERVE`, `OBJECT POSITION feed`,
`SET WINDOW` for 150 dpi grayscale/JPEG, `DEFINE SCAN MODE`, then `SCAN`.
Several `SCAN` payload variants were tested, including `00`, `01`, `00 01`,
`80`, `05`, `5a`, and paired variants. The scanner consistently returned a
trailer consistent with sense key `0x05` and ASC `0x26`, i.e. invalid field in
parameter list.

After these live attempts, offline disassembly showed that the harness's
original SANE-style `SET WINDOW` defaults did not exactly match Canon's
`ExecSetWindow` path. The harness was corrected to use driver-like defaults.

## Successful Live Duplex Scans

After physical recovery, `TEST UNIT READY` returned HTTP 200 again on
2026-05-09 19:19 CEST. The corrected duplex sequence then succeeded:

```text
reserve
object_position_feed
read_prescan_block
set_window_front
set_window_back
define_scan_mode_feed
define_scan_mode_buffer
define_scan_mode_color
scan
read_image_chunk...
cancel / discharge / release cleanup
```

Live capture artifacts:

```text
harness/captures/live-duplex/page-001.jpg           2550x3300 grayscale JPEG
harness/captures/live-duplex/page-002.jpg           2550x3300 grayscale JPEG
harness/captures/live-duplex/scan.pdf               PDF 1.4, 2 pages
harness/captures/live-duplex/scan-20260509-191943.bin
```

A second run with `--stop-after-frames 4` produced another valid two-page PDF:

```text
harness/captures/live-duplex-multipage/page-001.jpg 2550x3300 grayscale JPEG
harness/captures/live-duplex-multipage/page-002.jpg 2550x3300 grayscale JPEG
harness/captures/live-duplex-multipage/scan.pdf     PDF 1.4, 2 pages
harness/captures/live-duplex-multipage/scan-20260509-191957.bin
```

The second run reported sense `05/3a` and then `05/2c` after the two JPEG
frames, consistent with no further pages / command sequencing after the
available duplex sheet. The scanner still answered `GET SCANNER STATUS`
successfully afterward. The harness now stops image READ loops on these
no-more-image-data sense values instead of burning through the remaining chunk
budget.

## Successful 6-Sheet End-to-End Test

With six A4 sheets in the tray, a single `SCAN` with `--stop-after-frames 12`
still produced only one duplex sheet before `05/3a` / `05/2c` sense values.
Repeating the verified single-sheet duplex sequence five more times captured
the remaining sheets. The ordered outputs were assembled into one PDF:

```text
harness/captures/e2e-20260509-192542/pages/page-001.jpg .. page-012.jpg
harness/captures/e2e-20260509-192542/scan-12pages.pdf
```

Validation:

```text
page-001.jpg: 2550x3300 grayscale JPEG, 300 dpi
page-012.jpg: 2550x3300 grayscale JPEG, 300 dpi
scan-12pages.pdf: PDF document, version 1.4, 12 pages, 2.8 MB
```

The harness now includes a `--sheets` option to perform this repeated-sheet
workflow directly.

## Successful Unknown-Count / Unknown-Sidedness Test

The harness now includes `--scan-all`, which scans duplex one sheet at a time,
continues until the next sheet attempt returns no image frames, and drops blank
pages from the final PDF by default. Live command:

```sh
cd harness
uv run python scan_to_pdf.py --scan-all --execute-plan --experimental-scan \
  --output-dir captures/auto-e2e-20260509-195504 \
  --output-pdf captures/auto-e2e-20260509-195504/scan.pdf \
  --max-sheets 50 --max-chunks 10 --timeout 30
```

Result:

```text
sheet-01 .. sheet-05: two JPEG frames each
sheet-06: first image READ returned sense 05/3a, no JPEG frames
blank backs dropped: 5
kept pages: captures/auto-e2e-20260509-195504/pages/page-001.jpg .. page-005.jpg
final PDF: captures/auto-e2e-20260509-195504/scan.pdf
file: PDF document, version 1.4, 5 pages
```

The scanner reported healthy status afterward.

A second run after reseating the tray captured all six expected sheets:

```text
sheet-01 .. sheet-06: two JPEG frames each
sheet-07: feed/scan/read reported no-document / no-more-image-data sense
blank backs dropped: 6
kept pages: captures/auto-e2e-20260509-200013/pages/page-001.jpg .. page-006.jpg
final PDF: captures/auto-e2e-20260509-200013/scan.pdf
file: PDF document, version 1.4, 6 pages
```

The scanner again reported healthy status afterward.

A later A4/orientation-corrected end-to-end run used the default A4 window
(`2480x3508` at 300 dpi) and 180-degree final-page rotation:

```text
final PDF: captures/auto-a4-e2e-20260509-201134/scan.pdf
contact sheet: captures/auto-a4-e2e-20260509-201134/contact-sheet.jpg
file: PDF document, version 1.4, 8 pages
kept pages: 8 pages at 2480x3508, 300 dpi
blank backs dropped: 4
```

The contact sheet showed upright pages with the full A4 height. Two backs had
handwritten content and were correctly retained. The scanner reported healthy
status afterward.

## Recovery Notes

After one intentionally malformed no-data `SCAN` probe, `POST
/cgi-bin/cgiscsi` began timing out. ICMP ping, TCP port 80, and `GET /` still
showed that the embedded HTTP server was alive. Follow-up `TEST UNIT READY`
probes on 2026-05-09 still timed out, including after the corrected harness
sequence was prepared.

Latest recovery check on 2026-05-09 15:45 CEST:

```text
ping -c 2 <scanner-ip>                   -> 2/2 replies
GET http://<scanner-ip>/                 -> HTTP/1.1 404 from lighttpd/1.4.39
uv run python cgiscsi.py --timeout 5 tur  -> timed out waiting for CGI response
```

Rechecked on 2026-05-09 16:01 CEST:

```text
ping -c 1 <scanner-ip>                   -> 1/1 reply
uv run python cgiscsi.py --timeout 3 tur  -> timed out waiting for CGI response
```

Rechecked on 2026-05-09 16:07 CEST:

```text
ping -c 1 <scanner-ip>                   -> 1/1 reply
uv run python cgiscsi.py --timeout 3 tur  -> timed out waiting for CGI response
```

After physical recovery, rechecked on 2026-05-09 19:19 CEST:

```text
ping -c 1 <scanner-ip>                   -> reachable
uv run python cgiscsi.py --timeout 5 tur  -> HTTP 200, all-zero trailer
uv run python cgiscsi.py inquiry          -> CANON DR-C225 rev 1.06
uv run python cgiscsi.py status           -> 40 00 00 00 00 00 00 00
```

If this state recurs, the scanner may be recoverable through its web interface
restart function as well as by physical Stop/clear or power-cycle.

Observed web restart URL:

```text
http://<scanner-ip>/eng/private/mainte/restart_main.htm
```

Recovery verification command:

```sh
cd harness
uv run python cgiscsi.py --host <scanner-ip> --timeout 5 tur
```

## Future Hardware Crop / Deskew Probe

SANE's related `canon_dr` backend names scanner-side roller deskew and hardware
crop controls in its scan-mode structures. The current live-safe office
workflow handles edges with an A4 window, blank-back filtering, PDF rotation
metadata, and optional OCRmyPDF cleanup. A lower-priority reverse-engineering
test is to identify the corresponding DR-C225W II network command byte for:

- roller deskew
- hardware crop / auto paper edge detection

Probe one candidate bit at a time only after a known-good `TUR`/`INQUIRY`, use
a short `--max-chunks` limit, and be prepared to recover with:

```text
http://<scanner-ip>/eng/private/mainte/restart_main.htm
```

Do not fold unverified bits into the default workflow until a before/after scan
confirms the scanner still returns valid JPEG frames and the cgiscsi CGI remains
responsive.
