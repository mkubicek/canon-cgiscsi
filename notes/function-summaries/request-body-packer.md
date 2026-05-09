# Request Body Packer Summary

Source: `DRNetworkScanner`, Objective-C methods:

- `-[DRURLConnection toAscii:size:]`
- `-[DRURLConnection getMacAddress]`
- `-[DRURLConnection request:size:param:paramSize:recv:noDataTimeout:completionTimeout:]`

Pseudocode summary:

```text
def to_ascii_hex(buf):
    out = ""
    for byte in buf:
        out += format("%02.2hhx", byte)
    return out

body = "c="
body += to_ascii_hex(cdb_bytes)

if data_out_ptr is not NULL:
    body += format("&o&d=%@&dl=%d", to_ascii_hex(data_out), data_out_len)
else:
    body += format("&i&dl=%d", recv_len)

mac = get_en0_mac_as_12_lowercase_hex()
if mac is not NULL:
    body += format("&a=%@", mac)

request["Accept"] = "*/*"
request["Content-type"] = "application/x-www-form-urlencoded"
request["Content-length"] = decimal_string(len(body))
request.HTTPBody = body encoded as UTF-8
```

Notes:

- `CNetworkScanner` strips Canon's local 12-byte command/data container before
  calling this method.
- The `c=` and `d=` fields are hex strings, not URL-escaped binary.
- `getMacAddress` reads the macOS `en0` link-layer address via `sysctl`. Live
  scanner requests succeeded without the optional `&a=` field.
