# Troubleshooting

The safest first step is to separate adapter behavior from live scanner
behavior. Live scan control can move paper; keep hands clear of the feeder and
use live scans only when the loaded pages are safe to feed.

## Start With Mock Mode

Run the adapter without a scanner:

```sh
python -m airscan_adapter.server --mock --bind 127.0.0.1 --port 8080
```

Then check:

```sh
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/eSCL/ScannerCapabilities
```

If mock mode fails, fix the adapter environment before trying live hardware.

## Safe Live Checks

Use an explicit host and start with health/status endpoints:

```sh
python -m airscan_adapter.server --live --host scanner-host-or-ip \
  --allow-live-scans --bind 127.0.0.1 --port 8080
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/eSCL/ScannerStatus
```

These checks should not feed paper. A real `POST /eSCL/ScanJobs` can start the
ADF.

## Common Cases

Scanner found, but scan hangs:
Stop the client job if possible, wait for the adapter to return to idle, then
restart the adapter. If the scanner web UI still responds but `cgiscsi` does
not, restart or power-cycle the scanner before trying another live scan.

Scanner CGI stops responding after a malformed or failed scan:
Power-cycle the scanner, or open the maintenance restart page for the tested
DR-C225W II:

```text
http://scanner-host-or-ip/eng/private/mainte/restart_main.htm
```

ADF empty, ADF jam, or no pages returned:
Clear the feeder, reload a small test stack, and confirm `ScannerStatus` before
starting another scan. Use mock mode if the AirScan client still fails with no
paper involved.

Duplex scan returns blank backs:
Blank back removal is enabled by default. Disable it in config with
`blank_back_skip = false` if you need to inspect back-side output.

macOS Image Capture or Preview does not discover the adapter:
Confirm `/eSCL/ScannerCapabilities` works over HTTP first. Docker Desktop for
macOS does not naturally publish container mDNS onto the host network; run the
adapter on macOS directly or use a host-side Bonjour publisher.

Docker host networking and mDNS:
On Linux, host networking is the expected discovery path. Bridge networking may
let HTTP work while mDNS discovery fails. Do not bake private scanner IPs into
the image; provide the host through runtime config or environment. A healthy
container means the adapter HTTP process is answering, not that the scanner is
reachable.

Live scan works, but Image Capture cannot save where expected:
For Docker, make sure the mounted `/scans` directory is writable by the
container user. The default image runs as non-root UID `10001`.
