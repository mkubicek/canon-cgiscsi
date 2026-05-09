from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
import urllib.parse
from pathlib import Path


TRAILER = bytes(18)
DEFAULT_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300"
    "0302020302020303030304030304050805050404050a07070608"
    "0c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b10161011131415"
    "15150c0f171816141812141514ffdb0043010304040504050905"
    "0509140d0b0d1414141414141414141414141414141414141414"
    "1414141414141414141414141414141414141414141414141414"
    "141414141414ffc00011080008000803012200021101031101ff"
    "c4001400010000000000000000000000000000000000000008ff"
    "c4001410010000000000000000000000000000000000000000ff"
    "c4001401010000000000000000000000000000000000000000ff"
    "c4001411010000000000000000000000000000000000000000ff"
    "da000c03010002110311003f00b2c001ffd9"
)


class MockCgiscsiHandler(http.server.BaseHTTPRequestHandler):
    jpeg_payload = DEFAULT_JPEG
    requests: list[dict[str, list[str]]] = []

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/cgi-bin/cgiscsi":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("ascii")
        params = urllib.parse.parse_qs(body, keep_blank_values=True)
        self.requests.append(params)

        cdb_hex = params.get("c", [""])[0]
        cdb = bytes.fromhex(cdb_hex) if cdb_hex else b""
        data = self.response_data(cdb, params)

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data) + len(TRAILER)))
        self.end_headers()
        self.wfile.write(data + TRAILER)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("mock-cgiscsi: " + format % args + "\n")

    @classmethod
    def response_data(cls, cdb: bytes, params: dict[str, list[str]]) -> bytes:
        if not cdb:
            return b""
        opcode = cdb[0]
        expected_len = int(params.get("dl", ["0"])[0] or "0")
        if opcode == 0x12:
            inquiry = bytearray(0x60)
            inquiry[0] = 0x06
            inquiry[8:16] = b"CANON   "
            inquiry[16:32] = b"DR-C225         "
            inquiry[32:36] = b"1.06"
            return bytes(inquiry[:expected_len])
        if opcode == 0x25:
            payload = bytearray(0x34)
            payload[6:8] = bytes.fromhex("002c")
            return bytes(payload[:expected_len])
        if opcode == 0x28 and len(cdb) > 2 and cdb[2] == 0x00:
            return cls.jpeg_payload[:expected_len]
        return bytes(expected_len)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local mock Canon cgiscsi endpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--jpeg", type=Path, help="JPEG payload to return for image READ")
    args = parser.parse_args(argv)

    if args.jpeg:
        MockCgiscsiHandler.jpeg_payload = args.jpeg.read_bytes()

    with socketserver.TCPServer((args.host, args.port), MockCgiscsiHandler) as server:
        print(f"mock cgiscsi listening on http://{args.host}:{args.port}/cgi-bin/cgiscsi")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
