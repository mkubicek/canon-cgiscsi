# Scan Workflow Driver Summary

Source: `DRC225`, C++ symbols under `CCanoDR::*`, cross-referenced with SANE
`canon_dr`.

Pseudocode summary:

```text
open scanner object from network interface descriptor

test_unit_ready()
inquiry()
inquiry_vpd(page=0xf0)
reserve_unit()

if scanning from ADF:
    object_position(feed)

read(kind=6, len=0x80)  # pre-scan device block

if cached light/blank calibration does not match this scan mode:
    run calibration setup:
        set_window(calibration window one or two sides)
        define_scan_mode(page=0x32 buffer/source flags)
        define_scan_mode(page=0x36 dropout/color flags)
        scan(window_ids=[0xf3] or [0xf3, 0xf3])  # blank-space path
        read(data_type=image, calibration byte count)
        object_position(discharge)
        possibly set_adjust_data(version=3, len=0x28)

set_window(front window)
if duplex:
    set_window(back window)

define_scan_mode(page=0x30 feed/document flags)
define_scan_mode(page=0x32 buffer/source flags)
define_scan_mode(page=0x36 dropout/color flags)

if simplex front:
    scan(window_ids=[0x00])
elif simplex back:
    scan(window_ids=[0x01])
else:
    scan(window_ids=[0x00, 0x01])

while pages remain:
    while current page/side not complete:
        chunk = read(data_type=image, requested_len=line_aligned_chunk_len)
        append chunk to side/page image buffer
        inspect trailer sense/status for EOF/short-read conditions

    if more ADF pages:
        object_position(feed)
    else:
        object_position(discharge)
        break

release_unit()
```

Driver facts:

- `ExecSetWindow` sends command `0x24` and a 0x34-byte payload containing one
  0x2c-byte window descriptor.
- `ExecScan` sends command `0x1b` and a payload of one or two window IDs.
- `ExecRead` sends command `0x28`, data type at CDB byte 2, and 3-byte transfer
  length at CDB bytes 6..8.
- `ExecObjectPosition` uses opcode `0x31`; driver action arguments `0`, `1`,
  and `2` map to CDB byte 1 values `0x00`, `0x01`, and `0x04` respectively.
  Actions `0` and `1` are discharge/eject and feed/load. Action `2` appears in
  the `StartScan` recovery/reposition path; its exact public meaning is still
  labelled unknown.
- `ExecStopBatch` uses opcode `0xd8`.
- `ExecSetAdjustData` uses opcode `0xe1`, version byte `0x03`, a 3-byte
  big-endian length of `0x28`, and the version-3 coarse-calibration payload
  layout also documented by SANE.
- `ExecDefineScanMode` sends a 0x14-byte payload with page code at offset 4
  and page length `0x0e` at offset 5. The macOS binary maps mode 0 to page
  `0x30`, mode 1 to page `0x32`, and mode 2 to page `0x36`.
- The traced `StartScan` path runs the pre-scan read, `ExecSetWindow`, and the
  three `ExecDefineScanMode` calls. The actual `ExecScan` call is in
  `ScanPage`, which passes one byte for simplex (`00` front or `01` back) and
  two bytes for duplex (`00 01`).

Uncertainty:

- The exact DR-C225W II duplex image framing was not live-tested here. Related
  SANE code handles Canon scanners that return separate side streams and
  scanners that interlace duplex data.
- The exact user-facing meanings of several `DEFINE SCAN MODE` flag bytes are
  still partly inferred. The byte offsets and page construction are decoded.
