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

Run the mock server:

```sh
python -m airscan_adapter.server --bind 127.0.0.1 --port 8080 --mock
```

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
