# AirScan Adapter Docker Notes

Linux host networking is the supported Docker path for AirScan discovery:

```sh
docker build -f Dockerfile.airscan -t canon-cgiscsi-airscan .
docker run --rm --network host \
  -e CANON_CGISCSI_HOST=<scanner-host> \
  -v "$HOME/Scans:/scans" \
  canon-cgiscsi-airscan \
  python -m airscan_adapter.server --live --allow-live-scans --mdns \
    --bind 0.0.0.0 --port 8080 --allow-lan-bind
```

Use a config file or `CANON_CGISCSI_HOST`; do not bake a private LAN address
into the image. Keep `scanner.safe_mode = false` and
`scanner.allow_live_scans = true` only in the runtime config used for live
scanning.

## Trust model

The eSCL endpoint is unauthenticated, in line with sane-airscan and AirSane.
Any host that can reach the bound address can list jobs, drain pages from an
in-flight scan (`NextDocument` is a destructive pop), and trigger health
probes against the scanner. Treat the bind address as trusted: prefer a
loopback bind or a Docker network reachable only by trusted hosts. Run only
on networks where every other reachable host is allowed to use the scanner.

The server refuses to bind a non-loopback address unless `--allow-lan-bind`
is passed. The shipped container CMD passes the flag explicitly so the trust
decision is visible.

Docker Desktop on macOS is useful for HTTP development, but its VM does not
naturally publish Bonjour/mDNS onto the macOS host network in the way Image
Capture expects. For macOS AirScan discovery, run the adapter or a small
Bonjour publisher on the host.
