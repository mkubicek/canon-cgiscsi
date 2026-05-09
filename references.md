# References

## Canon Materials

Downloaded artifacts were kept under `downloads/` and extracted under
`extracted/`. Canon binaries are not committed or embedded in the deliverables.

| Artifact | Source URL | SHA-256 |
| --- | --- | --- |
| `DR-C225II_Driver_V1.1.12005.15001SP5_Windows.zip` | `https://gdlp01.c-wss.com/gds/0/0100009920/06/DR-C225II_Driver_V1.1.12005.15001SP5_Windows.zip` | `118bdd21f93d966c9f64dce0a5458cd2b6ed5193a21b4be30178d8a644905942` |
| `DR-C225W_II_NWMonitorV1.3.0.0_WCSetupToolV.1.3.0.0_forWindows.zip` | `https://gdlp01.c-wss.com/gds/5/0200005595/01/DR-C225W_II_NWMonitorV1.3.0.0_WCSetupToolV.1.3.0.0_forWindows.zip` | `517ca093c460bb89c1c3ae3eeae59638df22964d0da833b467e0f19c0c636b62` |
| `DR-C225_Driver_V.2.2.25.1031forMac.pkg` | `https://gdlp01.c-wss.com/gds/1/0100009921/11/DR-C225_Driver_V.2.2.25.1031forMac.pkg` | `33755a188ca23ee6f4e80c88abd727b494ab34ff1b22dfc91f0a486566e65a1f` |

Canon support pages used to locate downloads:

- Canon Europe DR-C225W II support page:
  `https://www.canon-europe.com/support/products/document-scanners/dr-series/imageformula-dr-c225w-ii.html`
- Canon Asia/Singapore DR-C225 II driver pages were used when the Europe page
  did not expose stable text download URLs in the local tooling.
- Canon Asia "Network Monitor and Connection Utility for Windows" page:
  `https://asia.canon/en/support/0200559501`

Key local binaries analyzed:

- `extracted/mac-v2.2/DR-C225 Driver.pkg/Payload/Library/Image Capture/TWAIN Data Sources/DRC225.ds/Contents/Frameworks/DRNetworkScanner.bundle/Contents/MacOS/DRNetworkScanner`
- `extracted/mac-v2.2/DR-C225 Driver.pkg/Payload/Library/Image Capture/TWAIN Data Sources/DRC225.ds/Contents/MacOS/DRC225`
- `extracted/windows-network-tool/DRC225W/Setup.exe`
- `extracted/windows-network-tool/DRC225W/NetworkMonitor/setup.exe`

The separate Windows network tool extracted to InstallShield bootstrapper
executables. `NetworkMonitor/setup.exe` contains an `ISSetupStream` overlay
starting at file offset `0x165a00`; normal PE resources contain installer UI
assets and text, not scanner protocol code. The bootstrapper strings and import
tables did not expose `cgiscsi`, `/cgi-bin/cgiscsi`, SCSI command names, or
Canon scan command construction.

## Prior Art

- SANE `canon_dr` backend source:
  `https://gitlab.com/sane-project/backends/-/blob/master/backend/canon_dr.c`
- SANE `canon_dr` command header:
  `https://gitlab.com/sane-project/backends/-/blob/master/backend/canon_dr-cmd.h`
- SANE `canon_dr` manual:
  `https://www.sane-project.org/man/sane-canon_dr.5.html`
- BasicCAT scanner protocol reverse-engineering writeup:
  `https://www.basiccat.org/reverse-engineer-document-scanner/`

Cached copies used for local cross-reference:

- `references-cache/canon_dr.c`
- `references-cache/canon_dr.h`
- `references-cache/canon_dr-cmd.h`
- `references-cache/sane-canon_dr.5.html`
- `references-cache/basiccat-scanner-re.html`

## OCR and PDF Output References

- Tesseract user manual:
  `https://tesseract-ocr.github.io/tessdoc/`
- Tesseract releases:
  `https://github.com/tesseract-ocr/tesseract/releases`
- Tesseract `tessdata_fast` model notes:
  `https://github.com/tesseract-ocr/tessdata_fast`
- OCRmyPDF project:
  `https://github.com/ocrmypdf/OCRmyPDF`
- OCRmyPDF optimizer documentation:
  `https://ocrmypdf.readthedocs.io/en/latest/optimizer.html`
- OCRmyPDF installation / optional optimizer dependencies:
  `https://ocrmypdf.readthedocs.io/en/latest/installation.html`
- img2pdf project:
  `https://gitlab.mister-muffin.de/josch/img2pdf`
- Apple Vision `RecognizeTextRequest`:
  `https://developer.apple.com/documentation/vision/recognizetextrequest`
- PaddleOCR documentation:
  `https://www.paddleocr.ai/main/en/index.html`
- PaddleOCR OCR pipeline:
  `https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html`
- PaddleOCR PP-OCRv5 notes:
  `https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5.html`

## Local Analysis Tools

- `pkgutil --expand-full` for the macOS `.pkg`
- `bsdtar` for ZIP/self-extracting package inspection
- `file`, `shasum -a 256`, `strings`, `otool`, `nm`, `/usr/bin/objdump`
- Local Python PE parsing for resource and overlay inventory
- `curl` for conservative live HTTP validation against `<scanner-ip>`

Unavailable locally:

- `7z`
- Ghidra `analyzeHeadless`
- `class-dump`

Because `class-dump` was unavailable, `notes/class-dump-output.txt` contains
Objective-C metadata extracted with `otool -ov` rather than native class-dump
syntax.
