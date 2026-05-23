# AirScan Adapter Docker Notes

For tagged releases, GHCR is the primary registry:

```sh
docker pull ghcr.io/mkubicek/canon-cgiscsi-airscan:0.1.0-experimental
```

The default image is offline-safe: it starts the adapter in mock mode, runs as a
non-root user, and does not include OCRmyPDF or Tesseract. OCR remains disabled
in the Docker example config unless a future OCR-enabled image adds those
dependencies.

Linux host networking is the supported Docker path for AirScan discovery:

```sh
docker build -f Dockerfile.airscan -t canon-cgiscsi-airscan .
cp docs/airscan/config.docker.example.toml airscan.toml
python -c 'import uuid; print(f"urn:uuid:{uuid.uuid4()}")'
# Edit airscan.toml: set scanner.host, replace the UUID placeholder with the
# generated urn:uuid value, set admin_url to the adapter LAN URL if publishing
# mDNS, and keep OCR disabled for the default image.
docker run --rm --network host \
  -v "$HOME/Scans:/scans" \
  -v "$PWD/airscan.toml:/config/airscan.toml:ro" \
  canon-cgiscsi-airscan \
  python -m airscan_adapter.server --config /config/airscan.toml \
    --live --mdns --allow-lan-bind
```

For a container-oriented starting point, use
[config.docker.example.toml](config.docker.example.toml). It sets
`paths.scan_inbox = "/scans"` and
`paths.spool_dir = "/tmp/canon-cgiscsi-airscan-spool"` so the config matches
the declared volume. Replace `scanner.host` at runtime, replace the UUID
placeholder with a stable `urn:uuid:<uuid>` value generated for your adapter
instance, and set `escl.admin_url` to the adapter's LAN URL if you want the
published admin link to be reachable from other hosts.

Use a config file or `CANON_CGISCSI_HOST`; do not bake a private LAN address
into the image. Keep `scanner.safe_mode = false` and
`scanner.allow_live_scans = true` only in the runtime config used for live
scanning. If the host-mounted scan directory is not writable by container UID
`10001`, either adjust the directory permissions or run the container with a
non-root `--user "$(id -u):$(id -g)"`.

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

The container healthcheck verifies that the adapter HTTP process answers
`/healthz`. It does not prove the physical scanner is reachable.

Docker Desktop on macOS is useful for HTTP development, but its VM does not
naturally publish Bonjour/mDNS onto the macOS host network in the way Image
Capture expects. For macOS AirScan discovery, run the adapter or a small
Bonjour publisher on the host.
