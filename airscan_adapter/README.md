# AirScan Adapter

This package is an offline-safe virtual AirScan/eSCL adapter around
`canon-cgiscsi`.

By default it uses a deterministic mock Canon backend. Live scanner access is
only available when explicitly enabled with config or `CANON_CGISCSI_HOST`; unit
tests do not open sockets to a scanner.

- eSCL `ScannerCapabilities` and `ScannerStatus` XML generation.
- Conservative `ScanSettings` parsing and validation.
- A mock Canon backend that yields deterministic JPEG pages.
- A single-job manager for page ordering, blank-page filtering, cancellation,
  and concurrency rejection.
- A standard-library HTTP server for `/eSCL/ScannerCapabilities`,
  `/eSCL/ScannerStatus`, `/eSCL/ScanJobs`, `NextDocument`, and DELETE.
- Optional OCR inbox PDF side effect that reuses the harness PDF/OCR helpers.
- `_uscan._tcp` TXT record generation and optional `zeroconf` publishing.

Install the runtime dep (required for safe XML parsing) before running tests
or the server:

```sh
pip install defusedxml
```

Run the mock server:

```sh
python -m airscan_adapter.server --bind 127.0.0.1 --port 8080 --mock
```

The server binds loopback by default. Binding a non-loopback address requires
`--allow-lan-bind` because the eSCL endpoint is unauthenticated. Request logs
go to stderr (Python's `BaseHTTPRequestHandler` default). On non-loopback
binds, `/healthz` and `/admin` avoid returning the configured scanner host or
detailed backend error strings.
That redaction is based on the adapter bind address; a reverse proxy in front
of a loopback-bound adapter is outside this simple policy.

Generate a starter config with a stable non-zero eSCL UUID for native runs:

```sh
python -m airscan_adapter.server --print-sample-config
```

Use the generated `escl.uuid` value for live/mDNS deployments. The built-in
default UUID is only for mock and development runs.

Live scans require a TOML config with `scanner.safe_mode = false` and
`scanner.allow_live_scans = true`, plus `scanner.host` or
`CANON_CGISCSI_HOST`. For one-off live validation, pass the host and explicit
live-scan acknowledgement on the command line:

```sh
python -m airscan_adapter.server --live --host <scanner-host-or-ip> \
  --allow-live-scans --bind 127.0.0.1 --port 8080
```

The adapter does not use Canon GUI apps, installers, Wine, or vendor
executables.

For Docker/Compose deployments that publish `_uscan._tcp` to other LAN
clients, set both the advertised admin URL and mDNS address. The CLI flag
`--admin-url "http://<adapter-lan-ip>:8080/admin"` controls the URL in eSCL
capabilities and TXT records. The environment variable
`AIRSCAN_ADVERTISE_IP=<adapter-lan-ip>` controls the IP address attached to the
mDNS service record. This is important on multi-interface Linux hosts and host
network containers, where the default host name may not resolve to the address
macOS Image Capture should open.
