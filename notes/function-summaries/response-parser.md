# Response Parser Summary

Source: `DRNetworkScanner`, C++ methods:

- `CNetworkScanner::ReadData(void*, unsigned int*, unsigned int, unsigned int)`
- `CNetworkScanner::WriteData(void*, unsigned int, unsigned int, unsigned int)`
- `CNetworkScanner::GetResponse(unsigned int*, unsigned int*)`

Pseudocode summary:

```text
if pending_command exists and pending_command.opcode == REQUEST_SENSE:
    if saved_14_byte_sense exists and caller_requested_at_least_14:
        copy saved sense to caller buffer
        caller_len = 14
        return DecodeIOResult(nil)

if pending_command exists and caller is reading:
    cdb = pending_command.bytes[12:]
    response = http_request(cdb, data_out=None, recv=caller_requested_len)
    clear pending_command

if pending_command exists and caller is writing data phase:
    cdb = pending_command.bytes[12:]
    data_out = current_write_buffer[12:]
    response = http_request(cdb, data_out=data_out, recv=0)
    clear pending_command

if response has data:
    stream response bytes to caller, excluding the final 18-byte trailer
    when only 18 response bytes remain:
        saved_sense = trailer[0:14]
        unknown_status_or_flags = little_endian_u32(trailer[14:18])
        clear HTTP scanData

GetResponse:
    if status_out_2 is not NULL:
        *status_out_2 = 0xffffffff
    *status_out_1 = 0x100000 if unknown_status_or_flags != 0 else 0
    return 0
```

Notes:

- The HTTP response body is data-in bytes followed by an 18-byte trailer.
- Status-only commands return only the 18-byte trailer.
- The first 14 trailer bytes are request-sense-like data.
- The final four bytes are not decoded beyond a non-zero test in the driver.
  Live successful responses ended with `00 00 00 02`.
