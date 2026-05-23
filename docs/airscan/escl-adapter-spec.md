# AirScan/eSCL Adapter Specification

This specification defines a virtual eSCL/AirScan server that presents a Canon
DR-C225W / DR-C225W II network scanner to macOS Image Capture, Preview,
`sane-airscan`, and similar clients, while using the `canon-cgiscsi` backend to
drive the physical scanner.

The adapter is a protocol bridge:

```text
AirScan/eSCL client
  HTTP + XML + mDNS
adapter server
  job state + spool + Canon backend
Canon DR-C225W cgiscsi endpoint
  POST /cgi-bin/cgiscsi
```

Default tests and development must use a mock Canon backend. Live scanner use
requires explicit host configuration and must never occur during unit tests.

## Goals

| Goal | Requirement |
| --- | --- |
| macOS discovery | Publish a Bonjour `_uscan._tcp` service with `rs=eSCL` and ADF/duplex TXT records. |
| ADF-first UX | Advertise ADF simplex/duplex, not platen, so Image Capture does not present a misleading flatbed-first scanner. |
| Multi-page scan | Internally scan one duplex Canon sheet at a time and serve one eSCL document per retained page through `NextDocument`. |
| Blank backs | Default to skip blank backs in the adapter policy; expose eSCL blank-page fields only once client behavior is verified. |
| Safety | Single active job, strict settings validation, fast cancellation, cleanup, and explicit wedged-scanner recovery. |
| OCR | Keep eSCL page transport compatible by serving JPEG pages; produce searchable PDF in a scan inbox as a side effect. |

## mDNS Advertisement

Publish plain HTTP first:

| Field | Value |
| --- | --- |
| Service type | `_uscan._tcp` |
| Instance name | `Canon DR-C225W AirScan` or configured model name |
| Port | configured eSCL HTTP port, default `8080` or `8090` |
| Root resource | `/eSCL` via TXT `rs=eSCL` |

Do not publish `_uscans._tcp` until real TLS is implemented end to end. A
reverse proxy may terminate HTTPS later, but TXT records and service type must
match actual reachability.

Example TXT records:

```text
txtvers=1
rs=eSCL
ty=Canon imageFORMULA DR-C225W II
note=Scan inbox adapter
pdl=image/jpeg
is=adf
duplex=T
cs=grayscale
adminurl=http://<adapter-host>:8080/admin
UUID=<stable-adapter-uuid>
mopria-certified-scan=1.2
```

V1 should not advertise `application/pdf` in `pdl` unless PDF `NextDocument`
behavior has been manually validated with macOS Image Capture and Preview.
Use the same stable adapter UUID in XML and mDNS, but format it for each
surface: `urn:uuid:<uuid>` in eSCL XML and bare `<uuid>` in the mDNS TXT
`UUID` value.

## HTTP Endpoints

| Method | Path | Purpose | Response |
| --- | --- | --- | --- |
| `GET` | `/eSCL/ScannerCapabilities` | Static/near-static capabilities. | `200 text/xml` |
| `GET` | `/eSCL/ScannerStatus` | Current scanner and job status. | `200 text/xml` |
| `POST` | `/eSCL/ScanJobs` | Start one scan job from `ScanSettings`. | `201 Created`, `Location: /eSCL/ScanJobs/<job-id>` |
| `GET` | `/eSCL/ScanJobs/<job-id>/NextDocument` | Return next page/document bytes. | `200 image/jpeg`, `503` while not ready, `404` when exhausted/unknown |
| `GET` | `/eSCL/ScanJobs/<job-id>/ScanImageInfo` | Optional actual page metadata for next/current page. | `200 text/xml` or `404` |
| `DELETE` | `/eSCL/ScanJobs/<job-id>` | Cancel or delete job. | `200`, `202`, or `404` |
| `GET` | `/admin` | Human health page. | `200 text/html` |
| `GET` | `/healthz` | Machine health. | `200/503 application/json` |
| `POST` | `/admin/scan-to-inbox` | Optional simple-user direct OCR workflow. | `202 application/json` |

Use `Cache-Control: no-store` on status, jobs, and documents.

### Curl Examples

These examples hit only the adapter, not the Canon scanner directly.

```sh
curl -s http://127.0.0.1:8080/eSCL/ScannerCapabilities
curl -s http://127.0.0.1:8080/eSCL/ScannerStatus
curl -i -H 'Content-Type: text/xml' \
  --data-binary @scan-settings.xml \
  http://127.0.0.1:8080/eSCL/ScanJobs
curl -o page-001.jpg \
  http://127.0.0.1:8080/eSCL/ScanJobs/job-000001/NextDocument
curl -X DELETE http://127.0.0.1:8080/eSCL/ScanJobs/job-000001
```

## XML Namespaces

Use namespace prefixes consistently:

```xml
xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"
xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
```

Do not rely on clients preserving prefixes in requests. Parse by namespace URI
and local name where possible.

## ScannerCapabilities

V1 minimal capability set:

```xml
<scan:ScannerCapabilities xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"
                          xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
  <pwg:Version>2.1</pwg:Version>
  <pwg:MakeAndModel>Canon imageFORMULA DR-C225W II AirScan Adapter</pwg:MakeAndModel>
  <pwg:Manufacturer>Canon</pwg:Manufacturer>
  <scan:UUID>urn:uuid:11111111-2222-4333-8444-555555555555</scan:UUID>
  <scan:AdminURI>http://adapter.local:8080/admin</scan:AdminURI>
  <scan:Adf>
    <scan:AdfSimplexInputCaps>
      <scan:MinWidth>1</scan:MinWidth>
      <scan:MaxWidth>2550</scan:MaxWidth>
      <scan:MinHeight>1</scan:MinHeight>
      <scan:MaxHeight>4200</scan:MaxHeight>
      <scan:SettingProfiles>
        <scan:SettingProfile>
          <scan:ColorModes><scan:ColorMode>Grayscale8</scan:ColorMode></scan:ColorModes>
          <scan:DocumentFormats>
            <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
          </scan:DocumentFormats>
          <scan:SupportedResolutions>
            <scan:DiscreteResolutions>
              <scan:DiscreteResolution>
                <scan:XResolution>300</scan:XResolution>
                <scan:YResolution>300</scan:YResolution>
              </scan:DiscreteResolution>
            </scan:DiscreteResolutions>
          </scan:SupportedResolutions>
        </scan:SettingProfile>
      </scan:SettingProfiles>
    </scan:AdfSimplexInputCaps>
    <scan:AdfDuplexInputCaps>
      <!-- Same profile as simplex for v1. -->
    </scan:AdfDuplexInputCaps>
  </scan:Adf>
</scan:ScannerCapabilities>
```

### Capability Policy

| Capability | Advertise v1? | Reason |
| --- | --- | --- |
| ADF simplex | yes | Maps to front-only Canon scan. |
| ADF duplex | yes | Core product goal and confirmed Canon JPEG path. |
| Platen | no | Device is sheet-fed; advertising platen can make macOS show wrong UX. |
| `image/jpeg` | yes | Confirmed scanner output and best AirScan compatibility. |
| `application/pdf` | no | May make clients wait for whole job; keep PDF as inbox side effect first. |
| Grayscale8 | yes | Confirmed default. |
| RGB24 | no initially | SANE and Canon suggest color paths, but not live-confirmed for adapter UX. |
| BlackAndWhite1 | no initially | Not validated and may degrade OCR/blank filtering. |
| 300 DPI | yes | Confirmed default. |
| 200/600 DPI | no initially | Add after live validation and timeout/size checks. |
| A4, Letter, Legal | yes | Current harness supports all three windows. |
| Brightness/contrast/gamma/etc. | no | Avoid unsupported UI controls. |
| Blank-page detection/removal | cautious | Implement policy internally; advertise after confirming clients request it sanely. |

## ScanSettings Parsing

Accept these v1 fields:

| XML field | Accepted values | Canon mapping |
| --- | --- | --- |
| `pwg:Version` | Any supported eSCL version; respond with adapter version. | No Canon mapping. |
| `scan:InputSource` | `Feeder`, missing defaults to `Feeder`. | ADF workflow. |
| `scan:Duplex` | `true`/`false`, default `true`. | Canon duplex or simplex scan payload. |
| `scan:XResolution`, `scan:YResolution` | `300` only in v1. | SET WINDOW DPI. |
| `scan:ColorMode` | `Grayscale8`, missing defaults to `Grayscale8`. | composition `2`, bpp `8`. |
| `pwg:DocumentFormat`, `scan:DocumentFormatExt` | `image/jpeg` only in v1. | Serve JPEG pages. |
| `scan:ScanRegions` | A4, Letter, Legal-sized full-page regions or absent. | Paper/window preset. |
| `scan:BlankPageDetection`, `scan:BlankPageDetectionAndRemoval` | Optional booleans. | Adapter policy; default configured. |

Reject unsupported settings with a clear eSCL job error and do not start a Canon
scan. Rejections must happen before any paper-motion command.

## ScannerStatus

Example idle status:

```xml
<scan:ScannerStatus xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"
                    xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
  <pwg:Version>2.1</pwg:Version>
  <pwg:State>Idle</pwg:State>
  <scan:AdfState>ScannerAdfLoaded</scan:AdfState>
</scan:ScannerStatus>
```

Status mapping:

| Adapter/backend condition | `pwg:State` | `scan:AdfState` | Notes |
| --- | --- | --- | --- |
| Ready, paper status unknown | `Idle` | omit or last known | Avoid making false paper claims. |
| Ready, paper loaded | `Idle` | `ScannerAdfLoaded` | Only if backend status supports it or last scan observed paper. |
| Ready, ADF empty | `Idle` | `ScannerAdfEmpty` | Use after no-page job or explicit safe status. |
| Scan in progress | `Processing` | `ScannerAdfProcessing` | Include active `JobInfo` if implemented. |
| Busy with another client/job | `Processing` | last known | Reject new `ScanJobs`. |
| Jam | `Stopped` | `ScannerAdfJam` | User action required. |
| Door open | `Stopped` | `ScannerAdfDoorOpen` | User action required. |
| Adapter can reach HTTP but cgiscsi times out | `Stopped` | omit | Admin page says cgiscsi wedged. |
| Scanner host unreachable | `Down` if client tolerates, otherwise `Stopped` | omit | Prefer HTTP 503 for new jobs. |

`sane-airscan` source shows practical clients parse `ScannerAdfLoaded`,
`ScannerAdfJam`, `ScannerAdfDoorOpen`, `ScannerAdfProcessing`, and
`ScannerAdfEmpty`, and retry transient HTTP 503 responses for `NextDocument`.

## Job Model

Only one active Canon job is allowed.

| State | Meaning | Allowed transitions |
| --- | --- | --- |
| `created` | POST accepted, job object allocated. | `validating`, `rejected`, `canceling` |
| `validating` | ScanSettings parsed and mapped before Canon commands. | `queued`, `rejected`, `canceling` |
| `queued` | Waiting for single scanner slot. V1 usually has no queue. | `scanning`, `canceling` |
| `scanning` | Canon backend running sheet loop. | `spooling`, `failed`, `canceling` |
| `spooling` | Pages available or OCR side effect running. | `completed`, `failed`, `canceling` |
| `completed` | All pages delivered or job exhausted. | terminal, delete cleanup |
| `failed` | Error mapped to user-visible message. | terminal, delete cleanup |
| `canceling` | DELETE received; cleanup in progress. | `canceled`, `failed` |
| `canceled` | Canon cleanup attempted. | terminal |
| `rejected` | Unsupported settings before live backend touch. | terminal |

### Sequencing

```text
POST /eSCL/ScanJobs
  parse ScanSettings
  if unsupported: reject before Canon backend
  allocate job id
  start background Canon sheet-loop task
  return 201 Location

GET /NextDocument
  if page spooled: return next image/jpeg
  if scan still running: return 503 Retry-After: 1
  if job complete and no pages left: return 404
  if job failed: return appropriate 4xx/5xx with eSCL error XML when useful

DELETE /ScanJobs/<id>
  set cancel event
  backend sends CANCEL/discharge/release best-effort
  remove or mark job canceled
```

`NextDocument` must be page-ordered:

```text
sheet 1 front -> page 1
sheet 1 back  -> page 2 unless blank removal drops it
sheet 2 front -> next page
sheet 2 back  -> next page unless blank removal drops it
```

## ScanImageInfo

Implement after basic `NextDocument` works. Minimal response:

```xml
<scan:ScanImageInfo xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
  <scan:JobUri>/eSCL/ScanJobs/job-000001</scan:JobUri>
  <scan:ActualWidth>2480</scan:ActualWidth>
  <scan:ActualHeight>3508</scan:ActualHeight>
  <scan:ActualBytesPerLine>2480</scan:ActualBytesPerLine>
  <scan:BlankPageDetected>false</scan:BlankPageDetected>
</scan:ScanImageInfo>
```

When blank removal is enabled and a page is dropped, do not expose the dropped
page through `NextDocument`; keep internal logs for audit.

## Error Mapping

| Backend error | HTTP/eSCL behavior | User message |
| --- | --- | --- |
| Unsupported settings | `400` or job `rejected` | "Unsupported scan settings. Retry with 300 DPI grayscale ADF." |
| Concurrent job | `409 Conflict` or `503 Retry-After` | "Scanner is busy with another scan." |
| ADF empty before any page | Job completes with no documents; `ScannerAdfEmpty` | "Load paper in the ADF and retry." |
| ADF empty after pages | Complete job normally. | No error. |
| Jam/double feed | `Stopped`, job failed | "Clear the paper path, reload pages, and retry." |
| Canon invalid parameter `05/26` | Job failed and backend marked suspect | "Adapter sent unsupported Canon settings. Retry defaults; report logs." |
| cgiscsi timeout | `503`, backend `wedged` | "Scanner did not answer cgiscsi. Power-cycle or restart scanner web UI." |
| OCR failure | eSCL page job succeeds; inbox OCR artifact marked failed with image PDF preserved. | "Scan saved; OCR failed. Original image PDF retained." |

## Admin and Health

Admin page fields:

| Field | Purpose |
| --- | --- |
| Adapter version and git commit | Support/debugging. |
| Scanner host | Confirm explicit backend target without private publishing. |
| eSCL URL and mDNS state | Client discovery debugging. |
| Last safe health check | Distinguish idle from unknown. |
| Last job | Duration, page count, blank pages dropped, OCR status. |
| Backend state | idle, scanning, busy, unreachable, wedged, recovery-needed. |
| Log path | Where to inspect adapter logs locally. |
| Recovery instructions | Clear paper, Stop, power-cycle, optional scanner web restart URL. |

`/healthz` should return JSON:

```json
{
  "adapter": "ok",
  "backend": "idle",
  "scanner_host": "<configured-host>",
  "active_job": null,
  "last_error": null
}
```

When bound to a non-loopback address, `/healthz` should omit private scanner
host and detailed backend error fields by default.

## Safety Rules For Implementation

1. No live scanner traffic in tests.
2. No Canon backend object may be constructed without explicit host config.
3. `POST /ScanJobs` validates settings before any Canon command.
4. Only a job can send paper-motion commands.
5. Automatic health checks may use only no-motion probes.
6. DELETE/cancel must be best effort and bounded by timeouts.
7. All live command paths must log high-level commands, not raw proprietary data or private scans.
8. Spool cleanup must remove raw streams unless configured for debugging.
