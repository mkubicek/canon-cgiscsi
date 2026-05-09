from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from commands import (
    cancel_cdb,
    define_scan_mode_buffer_payload,
    define_scan_mode_cdb,
    define_scan_mode_color_payload,
    define_scan_mode_feed_payload,
    get_memory_cdb,
    get_scanner_status_cdb,
    get_window_cdb,
    inquiry_cdb,
    object_position_action_cdb,
    object_position_cdb,
    read_cdb,
    read_kind_cdb,
    release_unit_cdb,
    request_sense_cdb,
    reserve_unit_cdb,
    set_adjust_data_cdb,
    set_window_cdb,
    set_window_payload,
    test_unit_ready_cdb,
)


TRAILER_LEN = 18


@dataclasses.dataclass(frozen=True)
class CgiscsiResponse:
    raw: bytes
    data: bytes
    sense: bytes
    unknown_status_or_flags_le32: int
    http_status: int


class CgiscsiClient:
    def __init__(self, host: str, *, scheme: str = "http", timeout: float = 30.0):
        self.host, self.scheme = self.normalize_host_and_scheme(host, scheme)
        self.timeout = timeout

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}/cgi-bin/cgiscsi"

    @staticmethod
    def hex_bytes(data: bytes) -> str:
        return data.hex()

    @staticmethod
    def normalize_host_and_scheme(host: str, scheme: str) -> tuple[str, str]:
        host = host.strip()
        if not host:
            raise ValueError("host must not be empty")
        if scheme not in {"http", "https"}:
            raise ValueError("scheme must be 'http' or 'https'")

        if "://" in host:
            parsed = urllib.parse.urlsplit(host)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("host URL must use http or https")
            if not parsed.netloc:
                raise ValueError("host URL must include a hostname")
            if parsed.path not in {"", "/", "/cgi-bin/cgiscsi"}:
                raise ValueError("host must be a host[:port], not a URL path")
            host = parsed.netloc
            scheme = parsed.scheme

        if any(char.isspace() for char in host) or "/" in host or "://" in host or "@" in host:
            raise ValueError("host must be a hostname or host:port")
        return host, scheme

    @staticmethod
    def parse_response(raw: bytes, http_status: int) -> CgiscsiResponse:
        if len(raw) < TRAILER_LEN:
            raise ValueError(f"cgiscsi response too short: {len(raw)} bytes")
        trailer = raw[-TRAILER_LEN:]
        return CgiscsiResponse(
            raw=raw,
            data=raw[:-TRAILER_LEN],
            sense=trailer[:14],
            unknown_status_or_flags_le32=int.from_bytes(trailer[14:18], "little"),
            http_status=http_status,
        )

    def build_body(
        self,
        cdb: bytes,
        *,
        data_out: bytes | None = None,
        data_in_len: int = 0,
        mac: str | None = None,
    ) -> str:
        body = "c=" + self.hex_bytes(cdb)
        if data_out is None:
            body += f"&i&dl={data_in_len}"
        else:
            body += f"&o&d={self.hex_bytes(data_out)}&dl={len(data_out)}"
        if mac:
            body += f"&a={mac.lower()}"
        return body

    def execute(
        self,
        cdb: bytes,
        *,
        data_out: bytes | None = None,
        data_in_len: int = 0,
        mac: str | None = None,
        pad_cdb_to_12: bool = True,
    ) -> CgiscsiResponse:
        if pad_cdb_to_12 and len(cdb) < 12:
            cdb = cdb + bytes(12 - len(cdb))

        body = self.build_body(cdb, data_out=data_out, data_in_len=data_in_len, mac=mac)
        body_bytes = body.encode("ascii")
        request = urllib.request.Request(
            self.url,
            data=body_bytes,
            headers={
                "Accept": "*/*",
                "Content-type": "application/x-www-form-urlencoded",
                "Content-length": str(len(body_bytes)),
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return self.parse_response(raw, response.status)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return self.parse_response(raw, exc.code)
            except ValueError as parse_exc:
                preview = raw[:128].hex()
                raise RuntimeError(
                    f"cgiscsi HTTP error {exc.code} with unparseable body: {preview}"
                ) from parse_exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"cgiscsi request failed: {exc}") from exc


def print_response(resp: CgiscsiResponse) -> None:
    print(f"http_status={resp.http_status}")
    print(f"raw_len={len(resp.raw)}")
    print(f"data_len={len(resp.data)}")
    print(f"sense={resp.sense.hex()}")
    print(f"unknown_status_or_flags_le32=0x{resp.unknown_status_or_flags_le32:08x}")
    if resp.data:
        print(f"data_head={resp.data[:64].hex()}")


def parse_hex_bytes(value: str) -> bytes:
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canon cgiscsi protocol harness")
    parser.add_argument(
        "--host",
        default=os.environ.get("CANON_CGISCSI_HOST"),
        help="scanner hostname or host:port; can also be set with CANON_CGISCSI_HOST",
    )
    parser.add_argument("--scheme", default="http", choices=["http", "https"])
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--no-pad",
        action="store_true",
        help="send CDB at natural SCSI length instead of Canon driver's 12-byte padded length",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("empty-probe")
    sub.add_parser("tur")
    sub.add_parser("request-sense")
    sub.add_parser("reserve")
    sub.add_parser("release")
    sub.add_parser("feed")
    sub.add_parser("eject")
    object_position = sub.add_parser("object-position")
    object_position.add_argument("action", type=lambda s: int(s, 0), choices=[0, 1, 2])

    inquiry = sub.add_parser("inquiry")
    inquiry.add_argument("--evpd", action="store_true")
    inquiry.add_argument("--page", type=lambda s: int(s, 0), default=0)
    inquiry.add_argument("--alloc", type=lambda s: int(s, 0), default=0x60)

    status = sub.add_parser("status")
    status.add_argument("--length", type=lambda s: int(s, 0), default=8)

    memory = sub.add_parser("memory")
    memory.add_argument("offset", type=lambda s: int(s, 0))
    memory.add_argument("length", type=lambda s: int(s, 0))

    read = sub.add_parser("read")
    read.add_argument("--type", type=lambda s: int(s, 0), default=0)
    read.add_argument("--uid", type=lambda s: int(s, 0), default=0)
    read.add_argument("--lid", type=lambda s: int(s, 0), default=0)
    read.add_argument("--length", type=lambda s: int(s, 0), default=0x10000)

    read_kind = sub.add_parser("read-kind")
    read_kind.add_argument("kind", type=lambda s: int(s, 0))
    read_kind.add_argument("--length", type=lambda s: int(s, 0), required=True)

    sub.add_parser("read-sensors")
    sub.add_parser("read-panel")
    sub.add_parser("read-counters")
    sub.add_parser("read-pixelsize")

    get_window = sub.add_parser("get-window")
    get_window.add_argument("--length", type=lambda s: int(s, 0), default=0x34)

    set_window = sub.add_parser("set-window")
    set_window.add_argument("--window-id", type=lambda s: int(s, 0), default=0)
    set_window.add_argument("--dpi-x", type=lambda s: int(s, 0), default=300)
    set_window.add_argument("--dpi-y", type=lambda s: int(s, 0), default=300)
    set_window.add_argument("--ulx-1200", type=lambda s: int(s, 0), default=0)
    set_window.add_argument("--uly-1200", type=lambda s: int(s, 0), default=0)
    set_window.add_argument("--width-1200", type=lambda s: int(s, 0), default=10200)
    set_window.add_argument("--height-1200", type=lambda s: int(s, 0), default=13200)
    set_window.add_argument("--composition", type=lambda s: int(s, 0), default=2)
    set_window.add_argument("--bits-per-pixel", type=lambda s: int(s, 0), default=8)
    set_window.add_argument("--brightness", type=lambda s: int(s, 0), default=0)
    set_window.add_argument("--threshold", type=lambda s: int(s, 0), default=0)
    set_window.add_argument("--contrast", type=lambda s: int(s, 0), default=0)
    set_window.add_argument("--reverse-padding", type=lambda s: int(s, 0), default=0x10)
    set_window.add_argument("--compression", type=lambda s: int(s, 0), default=0x80)
    set_window.add_argument("--compression-arg", type=lambda s: int(s, 0), default=3)
    set_window.add_argument("--vendor-unique-2a", type=lambda s: int(s, 0), default=0)

    define_mode = sub.add_parser("define-mode")
    define_mode.add_argument("page", choices=["feed", "buffer", "color"])
    define_mode.add_argument("--duplex", action="store_true")
    define_mode.add_argument("--async-buffer", action="store_true")
    define_mode.add_argument("--source-mode", type=lambda s: int(s, 0))
    define_mode.add_argument("--flag-05", action="store_true")
    define_mode.add_argument("--flag-06", action="store_true")
    define_mode.add_argument("--flag-0a", action="store_true")
    define_mode.add_argument("--interval", type=lambda s: int(s, 0), default=0)
    define_mode.add_argument("--param-04", action="store_true")
    define_mode.add_argument("--param-05", action="store_true")
    define_mode.add_argument("--param-06", action="store_true")
    define_mode.add_argument("--byte-0b", type=lambda s: int(s, 0), default=0)
    define_mode.add_argument("--byte-0c", type=lambda s: int(s, 0), default=0)
    define_mode.add_argument("--byte-0d", type=lambda s: int(s, 0), default=0)
    define_mode.add_argument("--byte-0e", type=lambda s: int(s, 0), default=0)
    define_mode.add_argument("--byte-11", type=lambda s: int(s, 0), default=0)
    define_mode.add_argument("--byte-12", type=lambda s: int(s, 0), default=0)

    set_adjust = sub.add_parser("set-adjust-data")
    set_adjust.add_argument("payload", type=parse_hex_bytes, help="0x28-byte coarse-calibration version-3 payload")
    set_adjust.add_argument("--version", type=lambda s: int(s, 0), default=3)

    raw = sub.add_parser("raw")
    raw.add_argument("cdb", type=parse_hex_bytes)
    raw.add_argument("--data-out", type=parse_hex_bytes)
    raw.add_argument("--data-in-len", type=lambda s: int(s, 0), default=0)
    raw.add_argument("--mac")

    sub.add_parser("cancel")

    args = parser.parse_args(argv)
    if not args.host:
        parser.error("--host is required, or set CANON_CGISCSI_HOST")
    client = CgiscsiClient(args.host, scheme=args.scheme, timeout=args.timeout)
    pad = not args.no_pad

    if args.command == "empty-probe":
        resp = client.execute(b"", data_in_len=0, pad_cdb_to_12=False)
    elif args.command == "tur":
        resp = client.execute(test_unit_ready_cdb(), data_in_len=0, pad_cdb_to_12=pad)
    elif args.command == "request-sense":
        resp = client.execute(request_sense_cdb(), data_in_len=14, pad_cdb_to_12=pad)
    elif args.command == "reserve":
        resp = client.execute(reserve_unit_cdb(), data_in_len=0, pad_cdb_to_12=pad)
    elif args.command == "release":
        resp = client.execute(release_unit_cdb(), data_in_len=0, pad_cdb_to_12=pad)
    elif args.command == "feed":
        resp = client.execute(object_position_cdb(feed=True), data_in_len=0, pad_cdb_to_12=False)
    elif args.command == "eject":
        resp = client.execute(object_position_cdb(feed=False), data_in_len=0, pad_cdb_to_12=False)
    elif args.command == "object-position":
        resp = client.execute(object_position_action_cdb(args.action), data_in_len=0, pad_cdb_to_12=False)
    elif args.command == "inquiry":
        resp = client.execute(
            inquiry_cdb(evpd=args.evpd, page=args.page, allocation=args.alloc),
            data_in_len=args.alloc,
            pad_cdb_to_12=pad,
        )
    elif args.command == "status":
        resp = client.execute(
            get_scanner_status_cdb(args.length),
            data_in_len=args.length,
            pad_cdb_to_12=False,
        )
    elif args.command == "memory":
        resp = client.execute(
            get_memory_cdb(args.offset, args.length),
            data_in_len=args.length,
            pad_cdb_to_12=pad,
        )
    elif args.command == "read":
        resp = client.execute(
            read_cdb(data_type=args.type, uid=args.uid, lid=args.lid, length=args.length),
            data_in_len=args.length,
            pad_cdb_to_12=pad,
        )
    elif args.command == "read-kind":
        resp = client.execute(
            read_kind_cdb(args.kind, length=args.length),
            data_in_len=args.length,
            pad_cdb_to_12=pad,
        )
    elif args.command == "read-sensors":
        resp = client.execute(read_cdb(data_type=0x8B, length=1), data_in_len=1, pad_cdb_to_12=pad)
    elif args.command == "read-panel":
        resp = client.execute(read_cdb(data_type=0x84, length=8), data_in_len=8, pad_cdb_to_12=pad)
    elif args.command == "read-counters":
        resp = client.execute(read_cdb(data_type=0x8C, length=0x80), data_in_len=0x80, pad_cdb_to_12=pad)
    elif args.command == "read-pixelsize":
        resp = client.execute(read_cdb(data_type=0x80, length=0x10), data_in_len=0x10, pad_cdb_to_12=pad)
    elif args.command == "get-window":
        resp = client.execute(
            get_window_cdb(args.length),
            data_in_len=args.length,
            pad_cdb_to_12=pad,
        )
    elif args.command == "set-window":
        payload = set_window_payload(
            window_id=args.window_id,
            dpi_x=args.dpi_x,
            dpi_y=args.dpi_y,
            ulx_1200=args.ulx_1200,
            uly_1200=args.uly_1200,
            width_1200=args.width_1200,
            height_1200=args.height_1200,
            composition=args.composition,
            bits_per_pixel=args.bits_per_pixel,
            brightness=args.brightness,
            threshold=args.threshold,
            contrast=args.contrast,
            reverse_padding=args.reverse_padding,
            compression=args.compression,
            compression_arg=args.compression_arg,
            vendor_unique_2a=args.vendor_unique_2a,
        )
        resp = client.execute(set_window_cdb(len(payload)), data_out=payload, pad_cdb_to_12=pad)
    elif args.command == "define-mode":
        if args.page == "feed":
            payload = define_scan_mode_feed_payload(
                param_04=args.param_04,
                param_05=args.param_05,
                param_06=args.param_06,
            )
        elif args.page == "buffer":
            payload = define_scan_mode_buffer_payload(
                duplex=args.duplex,
                async_buffer=args.async_buffer,
                source_mode=args.source_mode,
                flag_05=args.flag_05,
                flag_06=args.flag_06,
                flag_0a=args.flag_0a,
                interval=args.interval,
            )
        elif args.page == "color":
            payload = define_scan_mode_color_payload(
                byte_0b=args.byte_0b,
                byte_0c=args.byte_0c,
                byte_0d=args.byte_0d,
                byte_0e=args.byte_0e,
                byte_11=args.byte_11,
                byte_12=args.byte_12,
            )
        else:
            parser.error(f"unknown define-mode page {args.page}")
        resp = client.execute(define_scan_mode_cdb(len(payload)), data_out=payload, pad_cdb_to_12=pad)
    elif args.command == "set-adjust-data":
        if len(args.payload) != 0x28:
            parser.error("set-adjust-data payload must be exactly 0x28 bytes")
        resp = client.execute(
            set_adjust_data_cdb(version=args.version, payload_len=len(args.payload)),
            data_out=args.payload,
            pad_cdb_to_12=pad,
        )
    elif args.command == "raw":
        resp = client.execute(
            args.cdb,
            data_out=args.data_out,
            data_in_len=args.data_in_len,
            mac=args.mac,
            pad_cdb_to_12=pad,
        )
    elif args.command == "cancel":
        resp = client.execute(cancel_cdb(), data_in_len=0, pad_cdb_to_12=pad)
    else:
        parser.error(f"unknown command {args.command}")

    print_response(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
