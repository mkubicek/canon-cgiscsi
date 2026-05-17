# Canon Stack Map

This inventory is for clean-room interoperability work around Canon
imageFORMULA DR-C225W / DR-C225W II network scanning. It records what is known
from this repository, previously extracted local artifacts, and static-only
checks performed for the AirScan adapter plan. No Canon application was run and
no live scanner traffic was sent for this document.

## Brief Inventory

| Area | Current finding | Confidence | Evidence |
| --- | --- | --- | --- |
| Repository checkout | Small public tree with protocol notes, harness code, tests, and curated binary metadata. The only ignored local artifact currently visible is `harness/captures/`. | confirmed-repo | `rg --files`, `git status --ignored --short` |
| Installed Canon apps | `/Applications/CaptureOnTouch.app`, `/Applications/WebScanSettingTool.app`, and `/Applications/scanserver.app` were not present on this machine during this pass. | confirmed-repo | `stat`/`find` static inventory, 2026-05-14 |
| Installed Canon TWAIN bundles | `/Library/Image Capture/TWAIN Data Sources/DRC225*.ds` was not present on this machine during this pass. | confirmed-repo | `find '/Library/Image Capture/TWAIN Data Sources' ...`, 2026-05-14 |
| Installed Canon network prefs | `/Library/Preferences/Canon Electronics/Scanner drivers/NetworkDriver` was not present on this machine during this pass. | confirmed-repo | `stat`, 2026-05-14 |
| Main network protocol component | `DRNetworkScanner.bundle/Contents/MacOS/DRNetworkScanner` contains the HTTP/cgiscsi URL builder and request packer. | confirmed-driver | `notes/binary-inventory.tsv:13`, `notes/strings-output.txt`, `notes/function-summaries/url-builder.md` |
| Main scanner command component | `DRC225.ds/Contents/MacOS/DRC225` contains `CCanoDR::Exec*` scanner command methods and scan workflow logic. | confirmed-driver | `notes/binary-inventory.tsv:8`, `notes/function-summaries/scan-workflow-driver.md` |
| Canon scanserver app | Previously extracted `scanserver.app` is universal x86_64/arm64 and contains web/PDF/OCR-related frameworks, but it was not the strongest evidence source for `/cgi-bin/cgiscsi`. | confirmed-driver | `notes/binary-inventory.tsv:17-40`, `notes/strings-output.txt` |
| Network monitor package | Previously extracted Windows network monitor files looked like installer bootstrappers and did not expose cgiscsi/SCSI protocol strings in static sweeps. | confirmed-driver | `references.md`, `notes/binary-inventory.tsv:43-45` |
| Historical local repo | A local Trash checkout named `canon-c225w-client` existed and contained old captures/static extracts. Treat it as historical context only, not publishable material. | confirmed-repo | Redacted local-path inventory; not copied into this repo |

## Component Map

| Component | Location in evidence | Architecture | Role | Safe evidence source | Dangerous live path |
| --- | --- | --- | --- | --- | --- |
| TWAIN data source `DRC225.ds` | `extracted/mac-v2.2/.../DRC225.ds/Contents/MacOS/DRC225` | x86_64-only Mach-O bundle | Builds Canon SCSI CDBs, SET WINDOW payloads, scan mode pages, ADF workflow, cleanup, and calibration calls. | Symbols, strings, Objective-C/C++ metadata, summarized control flow. | Launching the TWAIN source or GUI can touch real scanner state. |
| `DRNetworkScanner.bundle` | `extracted/mac-v2.2/.../DRNetworkScanner.bundle/Contents/MacOS/DRNetworkScanner` | x86_64-only Mach-O bundle | Converts staged CDB/data buffers into `POST /cgi-bin/cgiscsi`, parses `<data><18-byte trailer>`, and caches sense bytes. | `otool`, `nm`, `strings`, Objective-C metadata, hashes. | Calling its methods from Canon UI can send live network requests. |
| `DRUSBScanner.bundle` | `extracted/mac-v2.2/.../DRUSBScanner.bundle/.../DRUSBScanner` | x86_64-only | USB transport sibling. Useful only to separate USB vs network concerns. | Binary inventory only. | USB scanner I/O if loaded by Canon stack. |
| `WrapperDS.ds` | `extracted/mac-v2.2/DriverNetworkMonitor.pkg/.../WrapperDS` | x86_64-only | Network driver wrapper/registration layer; no direct cgiscsi evidence in curated strings. | Static inventory. | Could influence Canon driver registration if installed/run. |
| `scanserver.app` | `extracted/mac-v2.2/scanserver.pkg/Payload/Applications/scanserver.app` | app binary universal x86_64/arm64 | Canon push-scan/web-server style app with PDF/OCR/web frameworks. | Static metadata; useful for deployment/architecture context. | Running it may start Canon services and modify scanner workflow. |
| `WebScanSettingTool.app` | `extracted/mac-v2.2/scanserver.pkg/Payload/Applications/WebScanSettingTool.app` | x86_64-only | Web/settings helper, likely scanner configuration UI. | Static strings and metadata only. | Running can modify device/network settings. |
| Windows SP5 driver | `downloads/DR-C225II_Driver_V1.1.12005.15001SP5_Windows.zip` and extracted payloads | PE32 installer stubs | Driver distribution context. No trusted network command evidence extracted locally. | Hashes and installer metadata. | Do not run installers or vendor executables. |
| Windows network monitor | `downloads/DR-C225W_II_NWMonitor...forWindows.zip` | PE32 installer stubs | Network monitor / Wi-Fi utility distribution. Static sweep did not find cgiscsi command construction. | Hashes, resource/strings summary. | Do not run installers or vendor executables. |

## Binary Architecture Summary

| Binary | SHA-256 | Architecture | Protocol relevance |
| --- | --- | --- | --- |
| `DRC225.ds/Contents/MacOS/DRC225` | `3f8801e535d4902f8b557801a7c6e32b3427cb64edaa66cbd8761e51774704d5` | x86_64 Mach-O bundle | High. Scanner lifecycle, command builders, sense decoding. |
| `DRNetworkScanner.bundle/Contents/MacOS/DRNetworkScanner` | `d64dfb38333bc61e3fe18d08a33a87103cc9240e6f7028dea7ba5410e2e26409` | x86_64 Mach-O bundle | High. cgiscsi HTTP envelope and trailer parsing. |
| `DRUSBScanner.bundle/Contents/MacOS/DRUSBScanner` | `c08e1757da6515d25c936a103ed2acd5f3c1a54fe2529c41bba765400a78231e` | x86_64 Mach-O bundle | Low for network adapter. |
| `WrapperDS.ds/Contents/MacOS/WrapperDS` | `5e518f4e7dea69540bce597e6ee9af4759c8c75c429d536ff365707819eefca1` | x86_64 Mach-O bundle | Medium for Canon registration, low for protocol. |
| `scanserver.app/Contents/MacOS/scanserver` | `99ed4364ee5271a1e38fc35af096fa7ad40c2b9b908cee09670704a1aa793293` | universal x86_64/arm64 | Medium for Canon app architecture, low for cgiscsi. |
| `scanserver_httpd.framework/.../scanserver_httpd` | `4112f43815ea0a19be455c18e14c54c62acb4f655b0e372e8e2ac800a033d895` | universal x86_64/arm64 | Low for cgiscsi; confirms Canon app includes an HTTP server component. |
| `WebScanSettingTool.app/Contents/MacOS/WebScanSettingTool` | `7155137c11c6cae92154f2b18d560fdb383ee654d32e08cea47ebbf94c691ece` | x86_64 Mach-O executable | Low for scan transport; web/settings helper. |

Full recorded binary metadata is in `notes/binary-inventory.tsv`.

## Which Components Know About `/cgi-bin/cgiscsi`

| Component | Finding | Confidence | Evidence |
| --- | --- | --- | --- |
| `DRNetworkScanner` | Builds `http` or `https` URL to `/cgi-bin/cgiscsi`, chooses HTTPS only when stored port is 443, and does not include an explicit port in the formatted URL. | confirmed-driver | `notes/function-summaries/url-builder.md`; `notes/strings-output.txt` |
| `DRNetworkScanner` | Packs bodies as `c=<hex-cdb>&i&dl=<len>` or `c=<hex-cdb>&o&d=<hex-data>&dl=<len>`, with optional MAC parameter. | confirmed-driver | `notes/function-summaries/request-body-packer.md`; `protocol-spec.md:121` |
| `DRC225` | Calls through generic scanner abstractions to issue command methods such as `ExecSetWindow`, `ExecScan`, `ExecRead`, `ExecStopBatch`, and `ExecSetAdjustData`. | confirmed-driver | `notes/function-summaries/scan-workflow-driver.md:61` |
| Windows network monitor setup | Curated static strings did not expose `cgiscsi`, `/cgi-bin`, SCSI command names, or scan command construction. | confirmed-driver | `notes/strings-output.txt`; `notes/binary-inventory.tsv:43-45` |

## Functional Ownership

| Function area | Likely owner in Canon stack | Current adapter replacement |
| --- | --- | --- |
| Discovery | Caller-supplied network descriptor plus external configuration; no decoded Canon broadcast path in `DRNetworkScanner`. | Explicit `--host`/config plus optional CIDR probing with INQUIRY; Bonjour will be for the virtual eSCL service, not Canon discovery. |
| Reservation | `DRC225` `ReserveScan` / `ExecReserveUnit`. | Canon backend adapter calls `RESERVE UNIT` per job and always releases in cleanup. |
| Status polling | `ExecGetScannerStatus`, `TEST UNIT READY`, selected `READ` status pages. | Backend health check returns eSCL `ScannerStatus` and admin health. No live status in default tests. |
| Window/setup | `ExecSetWindow` and `ExecDefineScanMode`. | Map eSCL settings to the already validated A4/Letter/Legal 300 DPI grayscale/JPEG window and scan mode payloads. |
| Scan start | `ExecScan` with payload window IDs. | Single active job; default duplex sheet-by-sheet capture with JPEG frames. |
| Image reads | `ExecRead` with data type `0x00`. | Backend read loop extracts JPEG frames; eSCL `NextDocument` returns one page at a time. |
| Cleanup/cancel | `ExecStopBatch`, `OBJECT POSITION discharge`, `RELEASE UNIT`. | Fast DELETE path triggers cancel, discharge when appropriate, release, and marks job terminal. |
| Calibration | Canon `BlankSpaceScan`, `AdjustLight`, `ExecSetAdjustData`, SANE `COR_CAL` prior art. | Do not implement new calibration in v1. Document facts; keep live calibration opt-in for future work only. |
| OCR/PDF | Canon app stack contains PDF/OCR frameworks; current public harness uses OCRmyPDF. | Adapter-side inbox PDF/OCR pipeline, independent from eSCL page transport. |

## Safe Evidence Sources

Use these freely for clean-room implementation:

| Source | Why safe |
| --- | --- |
| `README.md`, `protocol-spec.md`, `harness/*.py`, and `harness/tests/*.py` | Original public project code and docs. |
| `notes/function-summaries/*.md` | Clean-room summaries and pseudocode, not raw proprietary code. |
| `notes/binary-inventory.tsv` | File metadata, hashes, architectures, and short string/symbol summaries. |
| `notes/strings-output.txt` | Curated protocol-relevant strings, not broad proprietary dumps. |
| Upstream links in `references.md` | External references, with no vendored snapshots in this repo. |

Avoid committing or quoting from these:

| Source | Reason |
| --- | --- |
| Canon driver archives and extracted bundles | Proprietary vendor material. |
| Raw disassembly files in old/historical work trees | Proprietary-derived detail; summarize behavior only. |
| `harness/captures/` and older capture folders | May contain personal documents, OCR text, raw streams, and private network identifiers. |
| Windows installers or Canon GUI apps | Hard constraint: do not run during this task. |
