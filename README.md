# canon-cgiscsi

`canon-cgiscsi` is an unofficial Python client and protocol reference for
Canon's `cgiscsi` network document scanner interface, exposed by some
imageFORMULA DR scanners at:

```text
POST /cgi-bin/cgiscsi
```

The project was created to make otherwise unsupported network scanning usable on
modern systems. It contains clean-room protocol notes and a small executable
harness for discovery, status checks, ADF scanning, compact PDF assembly, and
optional searchable PDF output.

This repository is not affiliated with Canon.

## Current Status

Tested live:

| Model | Firmware / revision | Result |
| --- | --- | --- |
| Canon imageFORMULA DR-C225W II | `1.06` / `20140609` | Discovery, INQUIRY, status, control commands, ADF duplex JPEG capture, blank-back filtering, PDF assembly, and OCRmyPDF searchable output work. |

Likely related but untested:

- Canon DR-C225W and other networked imageFORMULA DR devices that expose
  `/cgi-bin/cgiscsi`.
- Other Canon DR network models may need per-model adjustments for scan-mode
  pages, read-kind fields, or vendor-specific payloads.

Not supported:

- USB-only Canon DR scanners. Use SANE's `canon_dr` backend instead.
- PIXMA, CanoScan, imageCLASS, and other non-DR Canon product families.

## Safety

This is low-level scanner control software. Malformed scan parameters can leave
the scanner's `cgiscsi` CGI unresponsive until the device is restarted. On the
tested DR-C225W II, recovery was possible by power-cycling or opening:

```text
http://scanner-host-or-ip/eng/private/mainte/restart_main.htm
```

For a new model, start with discovery, `INQUIRY`, and dry-run scan plans before
sending a live `SCAN`.

## Install

The harness uses `uv`:

```sh
cd harness
uv sync
```

Searchable PDF output is the default, so install OCRmyPDF, Tesseract, and
language data. On macOS:

```sh
brew install ocrmypdf tesseract-lang
```

Use `--no-ocr` when you want an image-only PDF or do not have OCR tools
installed.

## Quick Start

Set your scanner host once:

```sh
export CANON_CGISCSI_HOST="scanner-host-or-ip"
```

Confirm that the device answers `cgiscsi`:

```sh
uv run python discover.py --candidate "$CANON_CGISCSI_HOST"
uv run python cgiscsi.py inquiry
uv run python cgiscsi.py status
```

Print the scan plan without touching the scanner:

```sh
uv run python scan_to_pdf.py --scan-all --duplex --output-pdf captures/scan.pdf
```

Scan all sheets currently in the ADF. This always scans duplex, stops when the
ADF is empty, drops blank backs by default, and writes one compact searchable
PDF:

```sh
uv run python scan_to_pdf.py --execute-plan --experimental-scan \
  --scan-all --duplex \
  --output-dir captures/run-001 \
  --output-pdf captures/run-001/scan.pdf
```

Defaults are tuned for office documents:

- A4 scan window at 300 DPI.
- Grayscale JPEG from the scanner.
- Direct JPEG embedding via `img2pdf`, avoiding a second JPEG encode.
- PDF page rotation metadata instead of rewriting pixels.
- Compact OCRmyPDF searchable output by default, with `deu+eng+fra`.
- No raw streams, per-sheet PDFs, or extracted JPEGs are kept unless requested.

Useful overrides:

```sh
uv run python scan_to_pdf.py --no-ocr ...
uv run python scan_to_pdf.py --keep-intermediates ...
uv run python scan_to_pdf.py --paper letter ...
uv run python scan_to_pdf.py --scanner-compression-arg 2 ...
uv run python scan_to_pdf.py --crop-margin-px 25 ...
uv run python scan_to_pdf.py --keep-blank-pages ...
uv run python scan_to_pdf.py --ocr-clean --ocr-deskew ...
```

See [harness/README.md](harness/README.md) for the full command catalog.

## Protocol Documentation

- [protocol-spec.md](protocol-spec.md) describes the HTTP envelope, request and
  response framing, SCSI CDB layouts, Canon vendor-specific commands, and scan
  workflows.
- [harness/](harness/) contains the executable Python harness.
- [notes/function-summaries/](notes/function-summaries/) contains concise
  pseudocode summaries of the decoded Canon driver behavior.
- [references.md](references.md) records upstream materials and driver artifact
  hashes used during the interoperability analysis.

Canon driver archives, extracted binaries, raw disassembly, local scans, OCR
output, and cached third-party sources are intentionally not included.

## Development

Run the offline tests:

```sh
cd harness
uv run python -m unittest discover -s tests -v
```

Check the lockfile and syntax:

```sh
uv lock --check
uv run python -m py_compile cgiscsi.py commands.py discover.py mock_cgiscsi.py scan_to_pdf.py
```

Before publishing a repository, run the checklist in
[PUBLISHING.md](PUBLISHING.md).

## Legal

The repository publishes protocol facts, clean-room summaries, and
independently written Python code for interoperability. It does not redistribute
Canon binaries, extracted Canon code, raw proprietary disassembly, SANE source
snapshots, or private scan data.

Canon and imageFORMULA are trademarks of Canon Inc. Their names are used here
only to identify compatible hardware.

## References

This project cross-references:

- SANE `canon_dr` backend: https://gitlab.com/sane-project/backends/-/blob/master/backend/canon_dr.c
- SANE `sane-canon_dr` manpage: https://www.sane-project.org/man/sane-canon_dr.5.html
- BasicCAT scanner reverse-engineering writeup: https://www.basiccat.org/reverse-engineer-document-scanner/

See [references.md](references.md) for the complete source list.
