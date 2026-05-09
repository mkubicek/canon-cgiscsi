# cgiscsi Harness

This directory documents the protocol in executable Python form. It is not a
finished scanner frontend.

Set up with `uv`:

```sh
cd harness
uv venv
uv sync
```

Run offline regression tests:

```sh
uv run python -m unittest discover -s tests -v
```

Run the local mock cgiscsi endpoint:

```sh
uv run python mock_cgiscsi.py --port 18080
```

Then point the real client at it:

```sh
uv run python cgiscsi.py --host 127.0.0.1:18080 inquiry
uv run python discover.py --candidate 127.0.0.1:18080
uv run python scan_to_pdf.py --host 127.0.0.1:18080 --duplex \
  --execute-plan --experimental-scan --output-dir mock-captures \
  --output-pdf mock-captures/scan.pdf
```

Discover by probing candidate hosts with SCSI INQUIRY over cgiscsi:

```sh
uv run python discover.py --cidr 192.168.1.0/24 --timeout 0.5 --workers 32
uv run python discover.py --candidate <scanner-ip>
```

This is conservative protocol-level discovery. It does not implement an unknown
Canon broadcast protocol; it confirms reachable candidates by issuing INQUIRY
to `/cgi-bin/cgiscsi`.

Safe identification commands:

```sh
export CANON_CGISCSI_HOST=<scanner-ip>
uv run python cgiscsi.py --host <scanner-ip> empty-probe
uv run python cgiscsi.py --host <scanner-ip> tur
uv run python cgiscsi.py --host <scanner-ip> inquiry
uv run python cgiscsi.py --host <scanner-ip> status
```

`cgiscsi.py` and `scan_to_pdf.py` accept `--host`, or read
`CANON_CGISCSI_HOST` when the flag is omitted.

By default the harness pads CDBs to 12 bytes to match Canon's network driver.
Use `--no-pad` only when deliberately testing shorter standard CDB forms.
`empty-probe` sends `c=&i&dl=0`, and `tur` sends padded
`TEST UNIT READY` with `dl=0`. Do not use `dl=18` as a TUR health check.

EVPD page `0xf0` is documented from the Canon driver and SANE prior art, but a
live attempt timed out during this task:

```sh
uv run python cgiscsi.py --host <scanner-ip> inquiry --evpd --page 0xf0 --alloc 0x40
```

Verified non-image control commands:

```sh
uv run python cgiscsi.py --host <scanner-ip> reserve
uv run python cgiscsi.py --host <scanner-ip> release
uv run python cgiscsi.py --host <scanner-ip> feed
uv run python cgiscsi.py --host <scanner-ip> eject
uv run python cgiscsi.py --host <scanner-ip> read-sensors
uv run python cgiscsi.py --host <scanner-ip> read-panel
uv run python cgiscsi.py --host <scanner-ip> read-counters
uv run python cgiscsi.py --host <scanner-ip> read-kind 6 --length 0x80
uv run python cgiscsi.py --host <scanner-ip> set-window --window-id 0 --dpi-x 150 --dpi-y 150
uv run python cgiscsi.py --host <scanner-ip> get-window
```

The full decoded `OBJECT POSITION` action form is also exposed:

```sh
uv run python cgiscsi.py --host <scanner-ip> object-position 0  # discharge/eject
uv run python cgiscsi.py --host <scanner-ip> object-position 1  # feed/load
uv run python cgiscsi.py --host <scanner-ip> object-position 2  # decoded recovery/reposition action, not live-tested
```

Decoded but not live-tested calibration upload form:

```sh
PAYLOAD_HEX="<80 hex chars from the calibration routine>"
uv run python cgiscsi.py --host <scanner-ip> set-adjust-data \
  "$PAYLOAD_HEX"
```

The scanner's embedded `cgiscsi` CGI timed out after an intentionally malformed
`SCAN` experiment. If `tur` times out while `GET /` still returns HTTP, recover
the scanner before further live tests. Physical Stop/clear or power-cycle worked
during this investigation; the scanner can also be restarted through its web
interface when available:

```text
http://<scanner-ip>/eng/private/mainte/restart_main.htm
```

Print the planned ADF command sequence without touching the scanner:

```sh
uv run python scan_to_pdf.py --duplex
uv run python scan_to_pdf.py --duplex --image-format raw
```

Issue the setup commands only:

```sh
uv run python scan_to_pdf.py --duplex --execute-plan
```

`--execute-plan` stops before `SCAN` and cleans up. Add `--experimental-scan`
when pages are loaded and you want to perform live image acquisition.

Guarded duplex capture command:

```sh
uv run python scan_to_pdf.py --duplex --execute-plan --experimental-scan \
  --output-dir captures --output-pdf captures/scan.pdf --max-chunks 64 \
  --stop-after-frames 2
```

Batch duplex capture for a known number of sheets repeats the verified
single-sheet duplex workflow and assembles one ordered PDF:

```sh
uv run python scan_to_pdf.py --duplex --sheets 6 --execute-plan \
  --experimental-scan --output-dir captures/batch-6 \
  --output-pdf captures/batch-6/scan.pdf --max-chunks 10 \
  --stop-after-frames 2
```

Automatic ADF capture for an unknown number of sheets scans duplex, repeats
until the scanner reports the ADF is empty, drops blank pages by default, and
assembles one compact searchable PDF:

```sh
uv run python scan_to_pdf.py --scan-all --execute-plan \
  --experimental-scan --output-dir captures/auto \
  --output-pdf captures/auto/scan.pdf --max-sheets 100 \
  --max-chunks 10
```

This is the recommended path when you do not know whether the originals are
simplex or duplex. It always captures both sides and then filters pages whose
dark-pixel fraction is below `--blank-fraction-threshold` using
`--blank-pixel-threshold`. The default blank fraction threshold is `0.01`,
which drops the low-density blank backs observed on the DR-C225W II while
keeping sparse office pages in the validation set. Add `--keep-blank-pages` to
keep every captured front/back image in the final PDF.

The capture path now defaults to A4 geometry and marks kept PDF pages with a
180 degree rotation, matching the observed DR-C225W feed orientation without
re-encoding the scanner JPEG data. Use `--page-size letter` for Letter-sized
originals, `--rotate-degrees 0` to preserve scanner orientation, or explicit
`--width-1200` / `--height-1200` if you need a custom window.
`--paper a4|letter|legal` is the preferred paper preset flag; `--page-size`
remains as a compatibility alias.

Use `--crop-margin-px 25` only when you need deterministic border cleanup
without OCRmyPDF. Cropped pages are re-encoded as JPEGs, so the default remains
zero to preserve exact scanner JPEG passthrough.

During capture, the harness buffers the raw image stream, extracts complete
JPEG frames delimited by `ff d8` / `ff d9`, and builds the final PDF when frames
are found. By default those raw streams, extracted JPEGs, per-sheet PDFs, and
ordered page copies are temporary and are deleted after the final PDF is
written. Add `--keep-intermediates` when debugging protocol behavior or image
assembly. The workflow also attempts `CANCEL`, discharge, and release cleanup.
Use a higher `--stop-after-frames` value for multi-page ADF tests, or `0` to
rely only on `--max-chunks` / `--max-bytes`. Each sheet capture also has a
default `--max-bytes` cap of 64 MiB; raise it for high-resolution color/raw
experiments where one sheet can exceed that limit.

Final PDF assembly uses `img2pdf` to embed the scanner's JPEG bytes directly as
PDF `DCTDecode` image streams. This avoids Pillow's RGB conversion and second
JPEG encode. On the 8-page A4 OCR validation run, the PDF assembled from the
original scanner JPEGs with 180 degree PDF rotation was 1.12 MiB, compared with
3.12 MiB from the earlier Pillow path. The old `--pdf-jpeg-quality` flag is
accepted for compatibility but no longer controls JPEG PDF output; use
`--scanner-compression-arg` to experiment with the Canon SET WINDOW JPEG
compression byte instead.

Searchable PDF output is enabled by default. The default OCR mode is the compact
office-document path: OCRmyPDF adds a text layer but does not run `clean` or
`deskew`, because those steps transcode images and roughly doubled file size in
live validation. Use `--no-ocr` for an image-only PDF:

```sh
uv run python scan_to_pdf.py --scan-all --execute-plan --experimental-scan \
  --output-dir captures/auto --output-pdf captures/auto/scan.pdf \
  --max-sheets 100 --max-chunks 10 --no-ocr
```

Install the system tools and language data first:

```sh
brew install ocrmypdf tesseract-lang
```

Use `--ocr-output-pdf`, `--ocr-language`, `--ocr-optimize`,
`--ocr-tessdata-dir`, `--ocr-clean`, `--ocr-deskew`, or
`--no-ocr-rotate-pages` to tune the OCR stage.

For a quick macOS-only OCR comparison without adding Python dependencies, run:

```sh
uv run python scan_to_pdf.py --scan-all --execute-plan --experimental-scan \
  --keep-intermediates --output-dir captures/auto --output-pdf captures/auto/scan.pdf
swift tools/apple_vision_ocr.swift captures/auto/pages/page-*.jpg
```

This uses Apple's local Vision framework for diagnostics only; it does not yet
create a searchable PDF text layer. It needs `--keep-intermediates` because the
normal scan path deletes extracted JPEGs after writing the final PDF. It expects
upright JPEG inputs; current newly assembled PDFs apply rotation in PDF
metadata rather than rewriting the JPEG pixels.

For raw capture, select an uncompressed window and provide geometry if the raw
stream should be wrapped into a PDF:

```sh
uv run python scan_to_pdf.py --duplex --image-format raw --execute-plan \
  --experimental-scan --output-dir captures --output-pdf captures/raw.pdf \
  --raw-width 2550 --raw-height 3300 --raw-mode L --stop-after-frames 0 \
  --no-ocr --keep-intermediates
```

Assemble already-captured JPEG pages into a PDF:

```sh
uv run python scan_to_pdf.py --jpeg-to-pdf page-001.jpg page-002.jpg out.pdf
```

The output is searchable by default; add `--no-ocr` for image-only assembly.
The global `--rotate-degrees` default is `180`; add `--rotate-degrees 0` when
assembling JPEGs that are already upright.

The same `--rotate-degrees` option applies when reassembling existing JPEGs.
This can fix orientation in an old capture, but it cannot recover image content
that was cropped by a too-small scan window.

Assemble a raw raster into a PDF when geometry is known:

```sh
uv run python scan_to_pdf.py --raw-to-pdf page.raw out.pdf \
  --raw-width 2550 --raw-height 3300 --raw-mode L --no-ocr
```

Supported raw modes are `1` for packed 1-bit, `L` for 8-bit grayscale, and
`RGB` for 24-bit color. Add `--raw-stride` when each row has padding bytes.
