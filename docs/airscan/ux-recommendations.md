# AirScan UX Recommendations

The target workflow is simple:

```text
load ADF
choose "Canon DR-C225W AirScan" or click scan-to-inbox
receive a clean searchable PDF in the scan inbox
```

The adapter should feel like a first-class sheet-fed scanner, not a generic
network proxy.

## Default UX Profile

| Setting | Default | Reason |
| --- | --- | --- |
| Source | ADF | The DR-C225W is a document feeder scanner; flatbed UI is misleading. |
| Sides | Duplex | Captures unknown simplex/duplex input safely; blank-back filtering removes empty backs. |
| Resolution | 300 DPI | Confirmed and good for OCR, file size, and office documents. |
| Color | Grayscale8 | Confirmed scanner path and compact JPEG/PDF output. |
| Paper | A4 | Current project default and validated to avoid A4 cropping. |
| Alternate paper | Letter, Legal | Needed for US users and supported by harness presets. |
| Transport format | JPEG pages for eSCL | Most compatible with Image Capture and sane-airscan. |
| Output artifact | OCR searchable PDF in inbox | Best Milan/simple-user outcome. |
| OCR languages | `deu+eng+fra` | Current project default. |
| OCR clean/deskew | off by default | Keeps output compact and avoids unnecessary transcoding. |

## What To Hide Initially

| UI/capability | Hide until | Reason |
| --- | --- | --- |
| Flatbed/platen source | never, unless a real platen backend exists | Prevents wrong macOS source UI. |
| Color/RGB | live adapter validation confirms order, dimensions, file size, OCR behavior | SANE has DR-C225 color/interlace quirks; default network JPEG path is grayscale. |
| Black-and-white/lineart | validated output quality and threshold mapping | Bad threshold choices can harm OCR and blank detection. |
| 600 DPI | timeout/memory validation passes | eSCL clients may wait on pages; high DPI can stress scanner and adapter. |
| Brightness/contrast/gamma/sharpen | Canon mapping is validated | Exposing controls that do nothing creates bad UX. |
| PDF as eSCL `NextDocument` | macOS Image Capture/Preview validation passes | Whole-job PDF can create long waits and confusing progress. |
| Scanner-side calibration | explicit future safety plan exists | Calibration sends paper-motion and adjustment commands. |

## eSCL Client Behavior

Practical references:

| Client/reference | Observed behavior relevant to this adapter |
| --- | --- |
| AirSane README | AirSane targets Apple's Image Capture, sane-airscan, Windows eSCL, and Mopria clients; it publishes scanners through mDNS and serves JPEG/PNG/PDF/raster. |
| `sane-airscan` source | Parses ADF simplex/duplex capability blocks, color modes, document formats, resolutions, and ADF status. Retries transient `503` for `NextDocument`. |
| OpenPrinting `go-mfp` | Models `ScannerCapabilities`, `ScannerStatus`, `ScanSettings`, `ScanJobs`, `NextDocument`, and `ScanImageInfo` as eSCL resources. |
| Mopria eSCL public page | eSCL is the public scanner interface model for scanner engines and clients. |

Design implications:

1. Keep `/eSCL` as the root path.
2. Return `201 Created` and a usable `Location` from `POST /ScanJobs`.
3. Return `503 Retry-After: 1` while the first/next page is not yet ready.
4. Return `404` only when a job is exhausted or unknown.
5. Keep ADF status understandable and conservative.
6. Prefer a small, truthful capability set over a broad unvalidated one.

## OCR Strategy

Three designs were considered.

| Design | Behavior | Pros | Cons | Recommendation |
| --- | --- | --- | --- | --- |
| A. Standard eSCL page mode plus OCR side effect | Serve `image/jpeg` pages through `NextDocument`; adapter also assembles/OCRs a PDF in the scan inbox. | Highest AirScan compatibility, client sees pages quickly, OCR failure does not break scan, preserves simple inbox workflow. | User may see image-only PDF if saving from Image Capture; inbox PDF is a second artifact. | Default. |
| B. PDF-output mode | Advertise `application/pdf` and serve a whole searchable PDF. | One artifact through the client. | Client may wait for whole scan and OCR; timeout/progress behavior uncertain; harder cancellation. | Later experiment only. |
| C. Hybrid with explicit scan-to-inbox action | Keep eSCL JPEG page mode and add `/admin/scan-to-inbox` or launchd/CLI action that returns only inbox PDF. | Best for Milan/simple-user path; Image Capture remains compatible. | Requires small admin UI or helper action. | Implement with A. |

Default OCR pipeline:

```text
Canon JPEG pages
  -> ordered page spool
  -> img2pdf direct JPEG embedding
  -> OCRmyPDF text layer
  -> final searchable PDF in scan inbox
```

OCR settings:

| Setting | Default | Reason |
| --- | --- | --- |
| Engine | OCRmyPDF + Tesseract | Already integrated in `harness/scan_to_pdf.py`. |
| Languages | `deu+eng+fra` | Current project default. |
| Optimize | `1` | Compact default. |
| Output type | non-PDF/A `pdf` | Smaller and faster unless archival workflow explicitly needs PDF/A. |
| Clean | disabled | Live validation found cleanup/transcoding increased size. |
| Deskew | disabled | Same reason; enable only on request. |
| Rotate pages | enabled in OCRmyPDF | Helps text layer orientation. |
| Image rotation | PDF rotation metadata for final PDF when possible | Avoids re-encoding scanner JPEGs. |

OCR failure handling:

1. Never discard original JPEG pages until image PDF is created.
2. If OCR fails, keep the image-only PDF in the inbox.
3. Write a sidecar status file or admin job entry with OCR error details.
4. Surface "scan saved; OCR failed" instead of failing the eSCL scan.
5. Do not retry OCR indefinitely.

## Blank-Back Policy

Default behavior should match the existing harness:

| Step | Policy |
| --- | --- |
| Capture | Always scan duplex. |
| Detect | Compute dark-pixel fraction after margin crop/downsample. |
| Threshold | Start with `blank_pixel_threshold=245`, `blank_fraction_threshold=0.01`. |
| Drop | Drop pages classified as blank from final ordered output. |
| Audit | Log dropped page count and dark fraction, but do not retain raw images by default. |
| Override | Config `blank_back_skip=false` keeps every page. |

Do not advertise scanner-side blank-page removal as a Canon hardware feature.
This is an adapter policy implemented after capture.

## Status and Error Text

User-facing text should be direct and operational:

| Condition | Message |
| --- | --- |
| ADF empty before scan | `Load paper in the scanner ADF and try again.` |
| Jam | `Clear the paper path, reload the pages, and try again.` |
| Double feed | `Clear the double feed, check the stack, and try again.` |
| Busy | `Scanner is busy with another scan. Try again when it finishes.` |
| Unreachable | `Adapter cannot reach the scanner host. Check power and network.` |
| cgiscsi wedged | `Scanner web server responds, but cgiscsi does not. Power-cycle the scanner or use the scanner web restart page.` |
| Unsupported settings | `These scan settings are not supported yet. Use 300 DPI grayscale ADF.` |
| Canceled | `Scan canceled. Paper was discharged when possible.` |
| OCR failed | `Scan saved as image PDF. OCR failed; see admin status for details.` |

## Admin Page UX

The admin page is for fixing real-world failure states, not a marketing page.
It should show:

| Section | Contents |
| --- | --- |
| Scanner | configured host, model name, last safe health check, backend state |
| Service | eSCL URL, Bonjour instance, port, TLS status |
| Current job | state, elapsed time, current sheet, pages spooled, cancel button |
| Last job | page count, blanks dropped, PDF path, OCR status |
| Recovery | exact steps for ADF empty, jam, busy, unreachable, cgiscsi timeout |
| Logs | log directory and last error |

Do not expose buttons that run calibration or arbitrary Canon commands.

## Deployment UX

| Mode | UX recommendation |
| --- | --- |
| macOS launchd | Runs in user session or LaunchDaemon, publishes Bonjour on host network, writes to `~/Scans/Canon DR-C225W` by default. |
| Linux/Raspberry Pi | Runs as systemd service, Avahi publishes `_uscan._tcp`, writes to configured SMB/NFS/local inbox. |
| Docker on Linux | Use host networking for mDNS and scanner LAN reachability. |
| Docker Desktop on macOS | Avoid as primary mode; multicast DNS from containers is unreliable without host-side advertisement. Use a host process for Bonjour or run adapter directly on macOS. |

## Manual macOS Compatibility Checklist

Run only after the adapter works with a mock and after explicit approval for
live scanning:

| Check | Expected result |
| --- | --- |
| Bonjour discovery | Device appears as `Canon DR-C225W AirScan`. |
| App visibility | Image Capture and Preview show the device. |
| Type | Device appears as Bonjour/AirScan scanner. |
| Source | ADF/Feeder appears; flatbed does not dominate. |
| Duplex | Duplex option appears and works. |
| Multi-page | Multiple ADF sheets produce ordered pages. |
| Cancellation | Cancel stops reads and cleanup runs. |
| ADF empty | Empty tray produces understandable error. |
| Blank backs | Blank backs are skipped when policy enabled. |
| Inbox PDF | Searchable PDF appears in scan inbox. |
| Scanner health | Scanner remains responsive to safe health check after job. |

