# Changelog

All notable changes to this project will be documented here.

This project follows a pragmatic `0.x` release line while the protocol surface is
still being validated on more hardware.

## 0.1.0 - Unreleased

Initial public release.

- Documented Canon's `cgiscsi` HTTP/SCSI-over-CGI network scanner protocol.
- Added Python harness for device discovery, INQUIRY/status checks, control
  commands, ADF duplex scanning, JPEG extraction, blank-back filtering, PDF
  assembly, and OCR.
- Added compact searchable PDF output as the default scan artifact.
- Added opt-in debug output through `--keep-intermediates`.
- Added A4/Letter/Legal scan-window presets, with A4 as the default.
- Added OCRmyPDF integration using compact defaults: OCR text layer enabled,
  `clean` and `deskew` disabled unless requested.
- Tested live on Canon imageFORMULA DR-C225W II firmware/revision `1.06` /
  `20140609`.
