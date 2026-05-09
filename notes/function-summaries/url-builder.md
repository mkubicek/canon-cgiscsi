# URL Builder Summary

Source: `DRNetworkScanner`, Objective-C method
`-[DRURLConnection request:size:param:paramSize:recv:noDataTimeout:completionTimeout:]`.

Pseudocode summary:

```text
scheme = "https" if self.port == 443 else "http"
url = format("%@://%@/cgi-bin/cgiscsi", scheme, self.ipAddress)
request = NSMutableURLRequest(URL=url,
                              cachePolicy=reloadIgnoringLocalCacheData,
                              timeoutInterval=completionTimeout / 1000.0)
request.HTTPMethod = "POST"
```

Notes:

- The formatted URL does not include an explicit port component. The port is
  only used to choose `http` versus `https`.
- The scanner used for live validation answered plain HTTP at
  `http://<scanner-ip>/cgi-bin/cgiscsi`.
- `noDataTimeout` is accepted by the method signature but was not observed in
  the HTTP request construction.
