# AirScan Adapter Docker Notes

Linux host networking is the supported Docker path for AirScan discovery:

```sh
docker build -f Dockerfile.airscan -t canon-cgiscsi-airscan .
docker run --rm --network host \
  -e CANON_CGISCSI_HOST=<scanner-host> \
  -v "$HOME/Scans:/scans" \
  canon-cgiscsi-airscan \
  python -m airscan_adapter.server --live --allow-live-scans --mdns \
    --bind 0.0.0.0 --port 8080
```

Use a config file or `CANON_CGISCSI_HOST`; do not bake a private LAN address
into the image. Keep `scanner.safe_mode = false` and
`scanner.allow_live_scans = true` only in the runtime config used for live
scanning.

Docker Desktop on macOS is useful for HTTP development, but its VM does not
naturally publish Bonjour/mDNS onto the macOS host network in the way Image
Capture expects. For macOS AirScan discovery, run the adapter or a small
Bonjour publisher on the host.
