# AirScan Adapter Gap Analysis

This gap analysis separates what can be implemented now from what needs later
live validation. The immediate task did not perform new scanner experiments.

## Known Good Inputs

| Area | Known | Evidence |
| --- | --- | --- |
| Canon transport | HTTP POST form to `/cgi-bin/cgiscsi`, padded CDBs, 18-byte trailer. | `docs/protocol/canon-cgiscsi-full-spec.md`, `protocol-spec.md`, `harness/cgiscsi.py` |
| Device identity | Tested DR-C225W II returned `CANON DR-C225`, firmware/revision `1.06` / `20140609`. | `protocol-spec.md:70`, `README.md` |
| Default scan geometry | A4, 300 DPI, grayscale JPEG, front/back windows. | `harness/scan_to_pdf.py:34`, live validation notes |
| Duplex sheet capture | One duplex `SCAN` yields two JPEG frames for one sheet. | `protocol-spec.md:813`, `notes/live-control-log.md` |
| Multi-sheet workflow | Repeat one-sheet duplex workflow until no frames/ADF empty. | `harness/scan_to_pdf.py:682` |
| Blank-back filtering | Dark-pixel fraction filtering works for office docs in existing harness. | `harness/scan_to_pdf.py:216`, `harness/scan_to_pdf.py:748` |
| PDF assembly | img2pdf embeds scanner JPEGs directly; OCRmyPDF creates searchable PDF. | `harness/scan_to_pdf.py:82`, `harness/scan_to_pdf.py:102` |
| Cleanup | CANCEL, discharge, release best-effort sequence exists. | `harness/scan_to_pdf.py:433` |
| eSCL model | Endpoint names and XML resources are documented in public eSCL implementations. | OpenPrinting `go-mfp`, `sane-airscan`, AirSane, Mopria public page |

## Implementable Now

| Feature | Basis | Notes |
| --- | --- | --- |
| ADF-only eSCL `ScannerCapabilities` | eSCL model plus device reality | Do not advertise platen. |
| Grayscale JPEG `NextDocument` pages | Canon confirmed JPEG frames | Highest compatibility path. |
| Duplex default | Canon confirmed front/back windows and scan payload | Internally loop sheets. |
| Letter/Legal/A4 settings | Harness window presets | Add settings parse and validation. |
| Single active job | Safety requirement | Reject or queue concurrent jobs. |
| Cancellation endpoint | Canon cleanup command builders | Bounded best-effort cleanup. |
| OCR inbox side effect | Existing OCRmyPDF path | Keep eSCL scan success independent from OCR success. |
| Mock backend test suite | Existing mock patterns | Deterministic JPEG pages and status transitions. |
| Admin health page | Adapter-only state | No live scanner commands required to render. |
| mDNS TXT builder | Public AirScan/eSCL practice | Publish `_uscan._tcp` only for HTTP. |

## Needs Live Validation Later

| Question | Why it matters | Safe validation approach |
| --- | --- | --- |
| macOS Image Capture exact capability interpretation | ADF/duplex UI depends on XML details. | Start adapter with mock backend; check UI without scanner motion. |
| macOS behavior for `503 Retry-After` on `NextDocument` | Determines whether background scanning can lag page requests. | Mock server delays pages; observe client. |
| PDF `NextDocument` support | Could simplify output but may timeout. | Mock server returns generated PDFs before any Canon scan. |
| Simplex live path | Existing product goal includes simplex/front-only. | Use known-good settings; one sheet; explicit approval. |
| Color live path | Color may involve ordering/interlace quirks and larger files. | One-sheet color test, conservative max bytes/timeouts. |
| 200/600 DPI | Advertisable resolutions depend on performance and correctness. | One-sheet tests per DPI with stop limits. |
| ADF status decoding | Better paper-empty/jam/double-feed mapping needs exact status bytes. | Safe status reads only if approved; no paper motion. |
| Scanner busy/reservation conflicts | Needed for multi-client behavior. | Use adapter lock; later observe safe failures with no second live client. |
| Recovery action `OBJECT POSITION 0x04` | Could help jams but is paper motion. | Do not use until there is a recovery-specific safety plan. |
| Calibration paths | Could improve color/raw quality but high risk. | Keep out of v1; only explicit future research. |

## Unknowns

| Unknown | Current label | Adapter stance |
| --- | --- | --- |
| Meaning of final four cgiscsi trailer bytes | unknown | Store/log, do not overdecode. |
| Full `0xc5` status byte map | unknown/partial | Use conservative status and last job result. |
| `READ` types `0xa1`, `0xaa`, many `0x91` selectors | unknown | Do not use v1. |
| All `DEFINE SCAN MODE` flags | inferred/partial | Use existing zero/default builders only. |
| Exact Canon broadcast discovery | unknown | Use explicit host; adapter advertises itself via Bonjour. |
| Whether a single Canon `SCAN` can auto-feed multiple sheets in some firmware mode | unknown | Use confirmed one-sheet loop. |
| Raw mode width/stride/status metadata | partial | Do not expose raw over eSCL v1. |
| Double-feed detection mapping | unknown | Surface only when backend status/sense is decoded. |
| Secure AirScan `_uscans._tcp` requirements | partial | Do not publish until TLS is implemented and validated. |

## Canon vs SANE Gaps

| Topic | What SANE helps with | What SANE does not replace |
| --- | --- | --- |
| Command definitions | Canon DR opcode names, scan-mode pages, calibration structures. | cgiscsi HTTP envelope and 18-byte trailer. |
| DR-C225 quirks | Color/gray/duplex interlace warnings, calibration source, model-specific assumptions. | Confirmed network JPEG behavior and sheet-loop sequence. |
| ADF lifecycle | General SCSI scanner flow and cancel/read patterns. | The tested scanner's one-duplex-sheet-per-SCAN behavior. |
| Image processing | Concepts for cropping, deskew, dropout. | The adapter's compact JPEG/OCR PDF UX. |

## Safety Gaps

| Gap | Required before live use |
| --- | --- |
| Live adapter tests | Explicit user approval, known host, paper loaded intentionally, command list reviewed. |
| Recovery after wedge | Admin instructions and a no-motion health check; optional scanner web restart URL only as a documented user action. |
| Private scan data handling | Spool cleanup tests, ignored capture paths, no document text in logs. |
| Vendor artifacts | Publishing checklist must pass before public release. |

## Recommended V1 Boundary

V1 should implement:

1. ADF-only Bonjour/eSCL server.
2. Grayscale8, 300 DPI, JPEG page transport.
3. A4/Letter/Legal full-page windows.
4. Duplex default with blank-back skip.
5. Single active job and clear rejection of unsupported settings.
6. OCR searchable PDF in scan inbox as an adapter side effect.
7. Admin/health page with recovery instructions.
8. Full offline mock tests.

V1 should not implement:

1. Calibration.
2. Color or binary modes.
3. Scanner-side image enhancements.
4. PDF as the default eSCL document format.
5. Raw image eSCL transport.
6. Multiple simultaneous Canon jobs.
7. Hidden discovery scans across private networks by default.

