# Agent Instructions

These rules apply to automated agents and human contributors working in this
repository.

## Scope

This repository publishes clean-room protocol facts and independently written
Python code for Canon `cgiscsi` interoperability. Keep the public tree small,
auditable, and free of private or proprietary artifacts.

## Do Not Commit

- Canon driver archives or installer downloads.
- Extracted Canon binaries or installer contents.
- Raw disassembly, decompiler output, or copied proprietary source.
- Local scan captures, OCR text, PDFs, raw streams, or personal documents.
- Cached third-party source snapshots such as SANE files; link upstream instead.
- Virtual environments, local tessdata, caches, or generated `__pycache__`.

## Implementation Defaults

- Normal scan output should remain one compact searchable PDF.
- Do not leave raw streams, per-sheet PDFs, extracted JPEGs, or ordered page
  copies by default. Keep those behind `--keep-intermediates`.
- OCR should stay compact by default: text layer enabled, OCRmyPDF `clean` and
  `deskew` disabled unless explicitly requested.
- Preserve A4 as the default page window, with `--paper letter` and
  `--paper legal` available as overrides.
- Keep live scanner host configuration explicit via `--host` or
  `CANON_CGISCSI_HOST`; do not hardcode a private LAN address.

## Verification

Before finishing code changes, run:

```sh
cd harness
uv lock --check
uv run python -m unittest discover -s tests -v
uv run python -m py_compile cgiscsi.py commands.py discover.py mock_cgiscsi.py scan_to_pdf.py
```

For documentation-only changes, at least scan for private artifacts and local
identifiers before publishing:

```sh
rg -n "<private-ip>|<scanner-serial>|<private-name>|<private-address>"
```

Use `PUBLISHING.md` as the release checklist.
