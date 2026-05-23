# Canon Driver Evidence Log

This document records clean-room evidence used to plan an AirScan/eSCL adapter
around `canon-cgiscsi`. It intentionally contains summaries, hashes, symbol
names, file paths, and behavioral descriptions only. It does not contain Canon
source, raw disassembly, raw scan captures, credentials, SSIDs, serials, or
private network addresses.

## Reverse-Engineering Task List

| Task | Status | Next action |
| --- | --- | --- |
| Inventory current repo and ignored local artifacts | done | Keep `harness/captures/` ignored and do not inspect private contents. |
| Inventory installed Canon apps, TWAIN bundles, helpers, and prefs | done | Re-run if Canon software is reinstalled; no matching installed artifacts were present on 2026-05-14. |
| Identify binaries that construct `/cgi-bin/cgiscsi` | done | `DRNetworkScanner` is the primary evidence source. |
| Identify scanner lifecycle command builders | done | `DRC225` `CCanoDR::*` methods are the primary evidence source. |
| Consolidate full command catalog | done in this doc set | Keep `docs/protocol/canon-cgiscsi-full-spec.md` aligned with `harness/commands.py`. |
| Compare SANE `canon_dr` to Canon network behavior | done | Use SANE for command prior art, not for live network sequencing where the repo has stronger evidence. |
| Design eSCL adapter API and mDNS profile | done in this doc set | Use ADF-only minimal profile first. |
| Define OCR/searchable PDF UX | done in this doc set | Default to eSCL JPEG pages plus adapter-side OCR inbox PDF. |
| Implement offline-safe adapter skeleton | done | Keep it mock-backed until live validation is explicitly requested. |
| Live validate AirScan adapter | later, opt-in | Requires explicit user instruction and exact safety statement. |

## Static Commands Used

| Purpose | Command form | Result |
| --- | --- | --- |
| Repo file inventory | `rg --files -g '!*__pycache__*' -g '!*.pyc'` | Found public docs, harness code, tests, and notes. |
| Git state | `git status --short`, `git status --ignored --short` | Worktree was initially clean; ignored `harness/captures/` existed. |
| Installed Canon path check | `stat` and `find` over `/Applications`, `/Library/Image Capture/TWAIN Data Sources`, `/Library/Application Support/Canon Electronics`, and `/Library/Preferences/Canon Electronics/...` | No listed installed Canon apps, bundles, network monitor, or prefs were present. |
| Historical repo check | Redacted local-path inventory | Found a local Trash checkout named `canon-c225w-client`; used only as historical context. |
| Evidence search | `rg -n` for `cgiscsi`, command names, eSCL endpoints, Canon class/function names | Confirmed existing public notes and harness code cover the required Canon command facts; no AirScan code exists yet. |
| Upstream SANE check | `curl` public SANE source then `rg` for command names and DR-C225 model branches | Confirmed SANE command opcodes, SET SCAN MODE pages, DR-C225 interlace/color notes. |
| Upstream eSCL check | `curl`/web public `sane-airscan`, `go-mfp`, AirSane, Mopria pages | Confirmed endpoint names, ADF capability structure, retry behavior, mDNS service types, and client compatibility expectations. |

No `SCAN`, `OBJECT POSITION`, `SEND`, calibration, paper-motion, system
preference modification, Canon GUI launch, installer execution, Wine, or unknown
vendor executable was run during this task.

## Proprietary Binary Evidence

| Evidence item | SHA-256 | Finding | Confidence |
| --- | --- | --- | --- |
| `DRNetworkScanner` | `d64dfb38333bc61e3fe18d08a33a87103cc9240e6f7028dea7ba5410e2e26409` | Contains `DRURLConnection` Objective-C class, cgiscsi URL string, HTTP headers, body format strings, `CNetworkScanner` I/O methods. | confirmed-driver |
| `DRC225` | `3f8801e535d4902f8b557801a7c6e32b3427cb64edaa66cbd8761e51774704d5` | Contains `CCanoDR` command symbols for inquiry, reserve/release, window, scan, read/send, object position, stop/cancel, scanner status, adjust data, and memory. | confirmed-driver |
| `WrapperDS` | `5e518f4e7dea69540bce597e6ee9af4759c8c75c429d536ff365707819eefca1` | Network driver wrapper; no direct cgiscsi command construction in curated evidence. | confirmed-driver |
| Windows network tool `NetworkMonitor/setup.exe` | `b4d47eb6590ba69532daeb704a528ece6e75ee37e550d27f8f25932aa5536ca7` | InstallShield bootstrapper with no plain cgiscsi/SCSI command strings or PE resource evidence. | confirmed-driver |

Full binary inventory is in `notes/binary-inventory.tsv`.

## Key Driver Findings

| Finding | Confidence | Evidence |
| --- | --- | --- |
| Canon network transport is HTTP form-encoded SCSI-over-CGI at `POST /cgi-bin/cgiscsi`. | confirmed-wire, confirmed-driver | `protocol-spec.md:90`, `notes/function-summaries/url-builder.md`, `harness/cgiscsi.py:52` |
| Data-in form is `c=<hex-cdb>&i&dl=<len>`; data-out form is `c=<hex-cdb>&o&d=<hex>&dl=<len>`; optional `a=<mac>` exists but live requests succeeded without it. | confirmed-wire, confirmed-driver | `protocol-spec.md:121`, `notes/function-summaries/request-body-packer.md`, `harness/cgiscsi.py:96` |
| Canon driver pads most CDBs to 12 bytes before HTTP transmission. | confirmed-driver, confirmed-wire | `protocol-spec.md:149`, `harness/cgiscsi.py:113` |
| HTTP response body is data bytes plus an 18-byte trailer; first 14 bytes are sense-like and final four bytes are an unknown little-endian status/flags field. | confirmed-wire, confirmed-driver | `protocol-spec.md:195`, `notes/function-summaries/response-parser.md`, `harness/cgiscsi.py:83` |
| `REQUEST SENSE` is usually synthesized from the previous trailer rather than sent as a separate network command. | confirmed-driver | `protocol-spec.md:284`, `notes/function-summaries/response-parser.md` |
| `GET SCANNER STATUS` uses opcode `0xc5`, 12-byte CDB, and 8 data-in bytes; byte 0 bit `0x40` or byte 1 nonzero indicates busy/status-set in the driver. | confirmed-driver, confirmed-wire | `protocol-spec.md:637`, `harness/commands.py:202` |
| Live successful DR-C225W II INQUIRY identified `CANON DR-C225` revision `1.06` with date `20140609`. | confirmed-wire | `protocol-spec.md:70`, `notes/live-control-log.md` |
| Live duplex JPEG capture requires driver-like SET WINDOW defaults; earlier generic SANE-style defaults caused invalid-parameter sense. | confirmed-wire, confirmed-driver | `protocol-spec.md:923`, `notes/live-control-log.md`, `harness/commands.py:61` |
| One `SCAN` consumes one duplex sheet on the tested unit; multi-sheet workflow repeats the verified sheet sequence until no image data. | confirmed-wire | `protocol-spec.md:813`, `harness/scan_to_pdf.py:682` |
| No-more-image-data sense values observed after image reads are sense key `0x05` with ASC `0x3a` or `0x2c`. | confirmed-wire, confirmed-repo | `protocol-spec.md:804`, `harness/scan_to_pdf.py:330` |
| Cleanup attempts `CANCEL`, discharge, and release. | confirmed-repo | `harness/scan_to_pdf.py:433` |

## Evidence By Command

| Opcode | Name | Confidence | Evidence source |
| ---: | --- | --- | --- |
| `0x00` | TEST UNIT READY | confirmed-wire | `protocol-spec.md:272`, `harness/commands.py:18` |
| `0x03` | REQUEST SENSE | confirmed-driver | `protocol-spec.md:284`, `harness/commands.py:22` |
| `0x12` | INQUIRY | confirmed-wire | `protocol-spec.md:310`, `harness/commands.py:26` |
| `0x16` | RESERVE UNIT | confirmed-driver, confirmed-wire | `protocol-spec.md:341`, `harness/commands.py:30` |
| `0x17` | RELEASE UNIT | confirmed-driver, confirmed-wire | `protocol-spec.md:351`, `harness/commands.py:34` |
| `0x1b` | SCAN | confirmed-driver, confirmed-wire for validated JPEG path | `protocol-spec.md:361`, `harness/commands.py:38`, `harness/scan_to_pdf.py:338` |
| `0x24` | SET WINDOW | confirmed-driver, confirmed-wire | `protocol-spec.md:400`, `harness/commands.py:50` |
| `0x25` | GET WINDOW | confirmed-driver, confirmed-wire | `protocol-spec.md:470`, `harness/commands.py:54` |
| `0x28` | READ | confirmed-driver, confirmed-wire | `protocol-spec.md:484`, `harness/commands.py:103` |
| `0x2a` | SEND | confirmed-driver | `protocol-spec.md:556`, `harness/commands.py:137` |
| `0x31` | OBJECT POSITION | confirmed-driver, confirmed-wire for feed/discharge | `protocol-spec.md:587`, `harness/commands.py:173` |
| `0x3b` | GET MEMORY | confirmed-driver | `protocol-spec.md:620`, `harness/commands.py:192` |
| `0xc5` | GET SCANNER STATUS | confirmed-driver, confirmed-wire | `protocol-spec.md:637`, `harness/commands.py:202` |
| `0xd6` | DEFINE SCAN MODE | confirmed-driver | `protocol-spec.md:652`, `harness/commands.py:209` |
| `0xd8` | STOP BATCH / CANCEL | confirmed-driver, confirmed-repo cleanup | `protocol-spec.md:692`, `harness/commands.py:285` |
| `0xe1` | SET ADJUST DATA / COR_CAL | confirmed-driver, confirmed-SANE | `protocol-spec.md:704`, `harness/commands.py:146` |

## Canon vs SANE Evidence

| Topic | SANE `canon_dr` evidence | Canon network evidence | Adapter decision |
| --- | --- | --- | --- |
| Command opcodes | SANE defines standard Canon DR opcodes for TEST UNIT READY, REQUEST SENSE, INQUIRY, SCAN, SET WINDOW, READ, SEND, OBJECT POSITION, SET SCAN MODE, CANCEL, and COR_CAL. | Canon driver uses the same major opcodes inside cgiscsi. | Reuse command vocabulary and field sizes, but send through cgiscsi envelope. |
| SET SCAN MODE pages | SANE uses page codes such as `0x30`, `0x32`, `0x36`. | Canon `ExecDefineScanMode` maps its three modes to the same page codes and a 0x14 payload. | Use Canon-driver byte layout from `commands.py`; label semantic flags conservatively. |
| DR-C225 quirks | Current SANE source has a DR-C225 branch with color/gray/duplex interlace settings and fine calibration source. | Live network JPEG path returned separate complete JPEG frames in front/back order and did not need deinterlacing. | Do not implement SANE deinterlace in the default JPEG path; keep it as a raw/color future concern. |
| Calibration | SANE contains coarse/fine calibration logic and `COR_CAL`. | Canon driver has calibration paths, but current public harness does not require live calibration. | Do not run calibration in v1 adapter. Document and isolate future opt-in work. |
| ADF multi-page | SANE frontends often drive ADF by repeated load/read behavior. | Tested network path repeats one duplex `SCAN` per sheet until no images. | eSCL `NextDocument` should be backed by a per-job spool that performs Canon sheet loops internally. |

## External References Used

| Reference | Use |
| --- | --- |
| Mopria eSCL spec download page: https://mopria.org/spec-download | Confirms eSCL defines scan interfaces and is publicly available under Mopria license terms. |
| OpenPrinting `go-mfp` eSCL package: https://pkg.go.dev/github.com/OpenPrinting/go-mfp/proto/escl | Endpoint and XML model cross-check for ScannerCapabilities, ScannerStatus, ScanSettings, ScanJobs, NextDocument, ScanImageInfo. |
| `sane-airscan` source/docs: https://github.com/alexpevzner/sane-airscan and Debian source mirror | Practical client behavior, ADF capability parsing, `ScanJobs`/`NextDocument`, 503 retry behavior, ADF status parsing. |
| AirSane README: https://github.com/SimulPiscator/AirSane | Practical server behavior: mDNS, macOS Image Capture compatibility, JPEG/PNG/PDF/raster transfers, web UI. |
| SANE `canon_dr` source and manpage: https://gitlab.com/sane-project/backends/-/blob/master/backend/canon_dr.c and https://www.sane-project.org/man/sane-canon_dr.5.html | DR-family command, lifecycle, calibration, duplex/interlace prior art. |
