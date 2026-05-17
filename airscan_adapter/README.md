# AirScan Adapter Skeleton

This package is an offline-safe starting point for a virtual AirScan/eSCL
adapter around `canon-cgiscsi`.

It intentionally does not talk to a scanner, publish Bonjour records, or start
an HTTP listener. The current code covers the parts that can be tested without
hardware:

- eSCL `ScannerCapabilities` and `ScannerStatus` XML generation.
- Conservative `ScanSettings` parsing and validation.
- A mock Canon backend that yields deterministic JPEG pages.
- A single-job manager for page ordering, blank-page filtering, cancellation,
  and concurrency rejection.

The live backend, HTTP routes, mDNS publisher, OCR inbox pipeline, and admin
health page should be added in later phases from the documents under
`docs/airscan/`.

