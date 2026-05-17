# AirScan Adapter Implementation Plan

The adapter should be built in phases, starting with Python because the existing
Canon protocol reference is Python and already has command builders, a mock
server, PDF assembly, and OCR integration.

## Proposed Package Layout

```text
airscan_adapter/
  README.md
  pyproject.toml
  __init__.py
  config.py
  canon_backend.py
  mock_canon_backend.py
  escl_models.py
  jobs.py
  spool.py
  ocr.py
  mdns.py
  server.py
  admin.py
  logging.py
  tests/
    test_escl_models.py
    test_scan_settings.py
    test_capabilities.py
    test_status.py
    test_jobs.py
    test_next_document.py
    test_blank_filter.py
    test_ocr_fallback.py
    test_spool_cleanup.py
```

The package can initially import selected code from `harness/` or move shared
Canon code into a reusable module after tests pin behavior.

## Module Responsibilities

| Module | Responsibility |
| --- | --- |
| `config.py` | Load TOML/YAML/env config; validate safety flags and scanner host. |
| `canon_backend.py` | Adapter interface around `CgiscsiClient`; exposes `scan_pages(settings, cancel_event)` and safe health checks. |
| `mock_canon_backend.py` | Deterministic backend for tests and demos; no network. |
| `escl_models.py` | Dataclasses/enums plus namespace-safe XML serialization/parsing for capabilities, settings, status, jobs, and errors. |
| `jobs.py` | Single-job state machine, queue/reject policy, cancellation. |
| `spool.py` | Temporary page storage, ordered page metadata, retention cleanup. |
| `ocr.py` | img2pdf/OCRmyPDF inbox pipeline and failure fallback. |
| `mdns.py` | `_uscan._tcp` publisher abstraction; Avahi/zeroconf implementation. |
| `server.py` | HTTP routes for `/eSCL/*`. |
| `admin.py` | Minimal health/admin UI and scan-to-inbox action. |
| `logging.py` | Structured logs with redaction and job IDs. |

## Configuration

Example:

```toml
[scanner]
host = "scanner.local"
model_name = "Canon imageFORMULA DR-C225W II"
safe_mode = false
allow_live_scans = true

[escl]
bind = "0.0.0.0"
port = 8080
service_name = "Canon DR-C225W AirScan"
uuid = "00000000-0000-4000-8000-000000000000"
admin_url = "http://adapter.local:8080/admin"
tls = false

[scan_defaults]
paper = "a4"
dpi = 300
color_mode = "grayscale"
duplex = true
blank_back_skip = true
max_sheets = 100
max_bytes_per_sheet = 67108864

[ocr]
enabled = true
languages = "deu+eng+fra"
optimize = 1
clean = false
deskew = false
output_type = "pdf"

[paths]
scan_inbox = "~/Scans/Canon DR-C225W"
spool_dir = "~/Library/Caches/canon-cgiscsi-airscan/spool"
log_dir = "~/Library/Logs/canon-cgiscsi-airscan"
keep_intermediates = false
```

Safety semantics:

| Field | Meaning |
| --- | --- |
| `scanner.host` | Required for live backend; no hardcoded private LAN address. |
| `safe_mode=true` | Forces mock/no-motion mode even if host is configured. |
| `allow_live_scans=false` | Server can start and publish mock/admin status but rejects live ScanJobs. |
| `keep_intermediates=false` | Raw streams/JPEGs deleted after final artifacts unless debugging. |

## Phase Plan

| Phase | Scope | Exit criteria |
| --- | --- | --- |
| 0. Docs and skeleton | Docs in this directory; package skeleton optional. | Specs identify v1 behavior and safety boundaries. |
| 1. XML and model layer | `ScannerCapabilities`, `ScannerStatus`, `ScanSettings`, `JobInfo`, `ScanImageInfo` serialize/parse. | Unit tests validate XML snippets and unsupported setting rejection. |
| 2. Mock eSCL server | HTTP server with mock backend and fixed JPEG pages. | `curl` can create job and fetch multiple `NextDocument` pages offline. |
| 3. Job/spool state | Single active job, 503 while waiting, 404 when exhausted, DELETE cancel. | Unit/integration tests cover sequencing, cancellation, cleanup. |
| 4. Canon backend adapter | Wrap existing `scan_to_pdf` capture logic into generator yielding page bytes/metadata. | Mock tests remain default; live path guarded by explicit config. |
| 5. OCR inbox | Assemble image PDF and OCR PDF side effect; preserve fallback image PDF. | OCR failure test proves no page loss. |
| 6. mDNS | Publish `_uscan._tcp` with conservative TXT records. | `dns-sd -B _uscan._tcp` or `avahi-browse -rt _uscan._tcp` sees adapter. |
| 7. macOS validation | Manual Image Capture checklist with live scanner approval. | Device appears as ADF, duplex/multipage works, no wedge. |
| 8. Packaging | launchd/systemd/Docker docs and installers. | Repeatable install with explicit host config. |

## Canon Backend Interface

```python
class CanonBackend:
    def safe_health(self) -> BackendHealth:
        """No paper motion. TEST UNIT READY/status only when live enabled."""

    def scan_pages(
        self,
        settings: CanonScanSettings,
        cancel: threading.Event,
    ) -> Iterable[ScannedPage]:
        """Yield retained pages in order. Sends paper-motion only inside jobs."""

    def cancel_current(self) -> None:
        """Best-effort CANCEL/discharge/release with bounded timeouts."""
```

`scan_pages` should not expose raw stream paths. It should yield:

```python
@dataclass
class ScannedPage:
    index: int
    sheet_index: int
    side: Literal["front", "back"]
    mime_type: str
    data: bytes
    width_px: int | None
    height_px: int | None
    blank_detected: bool
```

## HTTP Server Choices

| Choice | Recommendation |
| --- | --- |
| Standard library `http.server` | Fine for early mock prototype; not ideal long term. |
| FastAPI/Starlette | Good developer velocity and admin UI; dependency weight acceptable. |
| aiohttp | Good streaming/cancellation model. |
| Flask | Simple but less natural for async job streaming. |

Recommendation: use `aiohttp` or FastAPI/Starlette. The Canon backend can run
in a worker thread because `urllib` and OCR are blocking. Keep one scanner lock
around live backend calls.

## Testing Strategy

Default tests must be offline-safe.

| Test area | Cases |
| --- | --- |
| XML serialization | Capabilities include ADF simplex/duplex and no platen; status states; image info. |
| ScanSettings parsing | Defaults, 300 DPI grayscale, paper size, duplex; reject color/600/PDF initially. |
| Job lifecycle | create, wait, page-ready, complete, delete, cancel during scan. |
| NextDocument | ordered pages, 503 while waiting, 404 when exhausted. |
| Duplex ordering | front/back, multi-sheet, blank back dropped. |
| Blank filtering | threshold policy with deterministic JPEG fixtures. |
| OCR fallback | OCR success writes final PDF; OCR failure preserves image PDF and marks error. |
| Spool cleanup | raw/intermediate files removed unless `keep_intermediates=true`. |
| Concurrency | second job rejected or queued predictably. |
| Admin health | JSON state and last error redaction. |
| mDNS builder | TXT records are generated correctly without requiring multicast in CI. |

Integration tests:

| Tool | Purpose |
| --- | --- |
| `curl` | Validate endpoint status, headers, Location, and documents. |
| Python HTTP client | Automated job flow with mock backend. |
| `sane-airscan` / `scanimage` optional | Validate Linux client behavior if installed. |
| macOS Image Capture manual | Final UX compatibility, not CI. |

## Deployment Modes

| Mode | Plan | Caveats |
| --- | --- | --- |
| macOS launchd | Run Python adapter on host network; publish Bonjour with Python `zeroconf` or system `dns-sd` helper; inbox under user `~/Scans`. | LaunchDaemon needs permissions for user inbox; LaunchAgent is simpler. |
| Linux/Raspberry Pi | systemd service, Avahi or Python `zeroconf`, host LAN access to scanner, inbox on local disk or share. | Ensure firewall permits adapter port and mDNS. |
| Docker on Linux | Host networking strongly recommended for multicast and scanner LAN. | Bridge networking often hides mDNS. |
| Docker Desktop on macOS | Use only for HTTP development; publish Bonjour from a host-side process or run adapter directly on host. | Docker Desktop VM does not naturally participate in host mDNS the way Image Capture expects. |
| Go appliance later | Reimplement stable eSCL server/backend once Python behavior is proven. | Keep Python first to avoid rewriting protocol while requirements are still moving. |

## Risk Register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| macOS ignores or misreads minimal capabilities | Device appears as flatbed or no duplex. | ADF-only capabilities; test with Image Capture; compare AirSane/sane-airscan behavior. |
| Client requests unsupported settings | Canon invalid-parameter or bad output. | Validate and reject before live backend commands. |
| Scanner CGI wedges | User cannot scan until recovery. | Single active job, known-good defaults, bounded cleanup, no calibration, admin recovery. |
| Long OCR blocks eSCL client | Client timeout. | Do OCR as side effect after/while JPEG pages are available. |
| Blank page false positive | Lost page content. | Conservative threshold, admin log, option to disable blank skip. |
| mDNS not visible across network | Client cannot discover. | Host networking, Avahi/Bonjour diagnostics, manual URL support for sane-airscan. |
| Private documents leak into repo/logs | Legal/privacy issue. | Keep captures ignored, log metadata only, no raw streams by default. |
| Proprietary artifacts committed | Legal issue. | Publishing checklist and private-artifact scan before release. |

## Definition of Done For V1

1. Mock adapter passes offline tests.
2. `/eSCL/ScannerCapabilities`, `/ScannerStatus`, `/ScanJobs`, `NextDocument`,
   and DELETE work with deterministic JPEGs.
3. mDNS TXT records are generated and publishable.
4. Live backend path is behind explicit config and single scanner lock.
5. ADF duplex scan produces ordered JPEG pages and inbox searchable PDF.
6. Cancellation attempts Canon cleanup.
7. Admin page shows host, state, last job, logs, and recovery instructions.
8. Documentation names all unsupported settings clearly.
