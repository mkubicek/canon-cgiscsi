from __future__ import annotations

import io
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from socketserver import TCPServer
from unittest.mock import patch

from PIL import Image, ImageDraw

from cgiscsi import CgiscsiClient, CgiscsiResponse
from commands import (
    define_scan_mode_buffer_payload,
    define_scan_mode_color_payload,
    define_scan_mode_feed_payload,
    get_scanner_status_cdb,
    get_window_cdb,
    object_position_action_cdb,
    read_kind_cdb,
    set_adjust_data_cdb,
    set_adjust_data_payload_v3,
    set_window_payload,
)
from discover import candidate_hosts_from_cidr, discover_candidates, parse_inquiry_identity
from scan_to_pdf import (
    A4_HEIGHT_1200,
    A4_WIDTH_1200,
    build_scan_plan,
    default_ocr_output_pdf,
    execute_auto_adf_capture,
    execute_sheet_batch_capture,
    execute_scan_capture,
    extract_jpegs,
    is_blank_jpeg_page,
    is_no_more_image_data_sense,
    jpeg_files_to_pdf,
    parse_ocr_languages,
    raw_file_to_pdf,
    run_ocrmypdf,
    sense_summary,
    write_output_jpeg,
)
from mock_cgiscsi import MockCgiscsiHandler


class QuietMockCgiscsiHandler(MockCgiscsiHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class FakeClient:
    def __init__(self, image_data: bytes):
        self.image_data = image_data
        self.calls: list[tuple[bytes, bytes | None, int, bool]] = []

    def execute(
        self,
        cdb: bytes,
        *,
        data_out: bytes | None = None,
        data_in_len: int = 0,
        pad_cdb_to_12: bool = True,
    ) -> CgiscsiResponse:
        self.calls.append((cdb, data_out, data_in_len, pad_cdb_to_12))
        data = b""
        if cdb[0] == 0x28 and cdb[2] == 0x00 and data_in_len:
            data = self.image_data
        elif data_in_len:
            data = bytes(data_in_len)
        return CgiscsiResponse(
            raw=data + bytes(18),
            data=data,
            sense=bytes(14),
            unknown_status_or_flags_le32=0,
            http_status=200,
        )


class FakeNoDocumentAfterOneReadClient(FakeClient):
    def __init__(self, image_data: bytes):
        super().__init__(image_data)
        self.image_reads = 0

    def execute(
        self,
        cdb: bytes,
        *,
        data_out: bytes | None = None,
        data_in_len: int = 0,
        pad_cdb_to_12: bool = True,
    ) -> CgiscsiResponse:
        self.calls.append((cdb, data_out, data_in_len, pad_cdb_to_12))
        if cdb[0] == 0x28 and cdb[2] == 0x00 and data_in_len:
            self.image_reads += 1
            if self.image_reads == 1:
                data = self.image_data + bytes(max(0, data_in_len - len(self.image_data)))
                sense = bytes(14)
            else:
                data = bytes(data_in_len)
                sense = bytes.fromhex("f000050000000006000000003a00")
        else:
            data = bytes(data_in_len) if data_in_len else b""
            sense = bytes(14)
        return CgiscsiResponse(
            raw=data + sense + bytes(4),
            data=data,
            sense=sense,
            unknown_status_or_flags_le32=0,
            http_status=200,
        )


class FakeAutoAdfClient(FakeClient):
    def __init__(self, sheet_streams: list[bytes]):
        super().__init__(b"")
        self.sheet_streams = sheet_streams
        self.sheet_index = 0
        self.image_reads_for_sheet = 0

    def execute(
        self,
        cdb: bytes,
        *,
        data_out: bytes | None = None,
        data_in_len: int = 0,
        pad_cdb_to_12: bool = True,
    ) -> CgiscsiResponse:
        self.calls.append((cdb, data_out, data_in_len, pad_cdb_to_12))
        if cdb[0] == 0x1B:
            self.sheet_index += 1
            self.image_reads_for_sheet = 0
        if cdb[0] == 0x28 and cdb[2] == 0x00 and data_in_len:
            self.image_reads_for_sheet += 1
            if self.sheet_index <= len(self.sheet_streams) and self.image_reads_for_sheet == 1:
                payload = self.sheet_streams[self.sheet_index - 1]
                data = payload + bytes(max(0, data_in_len - len(payload)))
                sense = bytes(14)
            else:
                data = bytes(data_in_len)
                sense = bytes.fromhex("f000050000000006000000003a00")
        else:
            data = bytes(data_in_len) if data_in_len else b""
            sense = bytes(14)
        return CgiscsiResponse(
            raw=data + sense + bytes(4),
            data=data,
            sense=sense,
            unknown_status_or_flags_le32=0,
            http_status=200,
        )


class CgiscsiEnvelopeTests(unittest.TestCase):
    def test_build_body_for_data_in_and_data_out(self) -> None:
        client = CgiscsiClient("scanner.example")
        padded_inquiry = bytes.fromhex("120000006000000000000000")
        self.assertEqual(client.build_body(b"", data_in_len=0), "c=&i&dl=0")
        self.assertEqual(
            client.build_body(padded_inquiry, data_in_len=0x60),
            "c=120000006000000000000000&i&dl=96",
        )
        self.assertEqual(
            client.build_body(bytes.fromhex("1b0000000200"), data_out=b"\x00\x01"),
            "c=1b0000000200&o&d=0001&dl=2",
        )

    def test_parse_response_splits_data_sense_and_flags(self) -> None:
        sense = bytes.fromhex("f000050000000006000000002600")
        raw = b"data" + sense + (0x02000000).to_bytes(4, "little")
        response = CgiscsiClient.parse_response(raw, 200)
        self.assertEqual(response.data, b"data")
        self.assertEqual(response.sense, sense)
        self.assertEqual(response.unknown_status_or_flags_le32, 0x02000000)

    def test_host_url_is_normalized(self) -> None:
        client = CgiscsiClient("http://scanner.local:8080/cgi-bin/cgiscsi")
        self.assertEqual(client.host, "scanner.local:8080")
        self.assertEqual(client.scheme, "http")
        self.assertEqual(client.url, "http://scanner.local:8080/cgi-bin/cgiscsi")
        with self.assertRaises(ValueError):
            CgiscsiClient("http://scanner.local/not-cgiscsi")

    def test_http_error_body_is_preserved_when_cgiscsi_framed(self) -> None:
        body = b"diag" + bytes.fromhex("f000050000000006000000002600") + bytes(4)
        error = urllib.error.HTTPError(
            "http://scanner.example/cgi-bin/cgiscsi",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(body),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            response = CgiscsiClient("scanner.example").execute(b"\x00", pad_cdb_to_12=False)
        self.assertEqual(response.http_status, 500)
        self.assertEqual(response.data, b"diag")
        self.assertEqual(response.sense.hex(), "f000050000000006000000002600")


class CommandBuilderTests(unittest.TestCase):
    def test_verified_control_cdbs(self) -> None:
        self.assertEqual(get_scanner_status_cdb().hex(), "c50000000800000000000000")
        self.assertEqual(get_window_cdb(0x1234).hex(), "25000000000000123400")
        self.assertEqual(read_kind_cdb(6, length=0x80).hex(), "28008c00000000008000")
        self.assertEqual(object_position_action_cdb(0).hex(), "31000000000000000000")
        self.assertEqual(object_position_action_cdb(1).hex(), "31010000000000000000")
        self.assertEqual(object_position_action_cdb(2).hex(), "31040000000000000000")

    def test_define_scan_mode_pages(self) -> None:
        self.assertEqual(define_scan_mode_feed_payload().hex(), "00000000300e0000000000000000000000000000")
        self.assertEqual(define_scan_mode_feed_payload(param_04=True)[7], 0x01)
        self.assertEqual(define_scan_mode_feed_payload(param_05=True)[7], 0x04)
        self.assertEqual(define_scan_mode_feed_payload(param_04=True, param_05=True)[7], 0x05)
        self.assertEqual(
            define_scan_mode_buffer_payload(duplex=True).hex(),
            "00000000320e0201000000000000000000000000",
        )
        self.assertEqual(define_scan_mode_color_payload().hex(), "00000000360e0000000000000000000000000000")

    def test_driver_like_window_defaults(self) -> None:
        payload = set_window_payload(window_id=0)
        desc = payload[8:]
        self.assertEqual(payload[6:8], bytes.fromhex("002c"))
        self.assertEqual(desc[0], 0x00)
        self.assertEqual(desc[0x16], 0x00)
        self.assertEqual(desc[0x18], 0x00)
        self.assertEqual(desc[0x19], 0x02)
        self.assertEqual(desc[0x1D], 0x10)
        self.assertEqual(desc[0x20], 0x80)

    def test_set_adjust_data_version_3_layout(self) -> None:
        self.assertEqual(set_adjust_data_cdb().hex(), "e1000000000300002800")
        payload = set_adjust_data_payload_v3(
            front_gain=(1, 2, 3),
            front_offset=(4, 5, 6),
            front_exposure=(0x0102, 0x0304, 0x0506),
            back_gain=(7, 8, 9),
            back_offset=(10, 11, 12),
            back_exposure=(0x0708, 0x090A, 0x0B0C),
        )
        self.assertEqual(len(payload), 0x28)
        self.assertEqual(payload[0x00:0x0E].hex(), "0102030004050600010203040506")
        self.assertEqual(payload[0x14:0x22].hex(), "070809000a0b0c000708090a0b0c")


class DiscoveryTests(unittest.TestCase):
    def test_parse_inquiry_identity(self) -> None:
        inquiry = bytearray(0x60)
        inquiry[0] = 0x06
        inquiry[8:16] = b"CANON   "
        inquiry[16:32] = b"DR-C225         "
        inquiry[32:36] = b"1.06"
        self.assertEqual(parse_inquiry_identity(bytes(inquiry)), (0x06, "CANON", "DR-C225", "1.06"))

    def test_candidate_hosts_from_cidr(self) -> None:
        self.assertEqual(candidate_hosts_from_cidr("192.0.2.10/32"), ["192.0.2.10"])
        self.assertEqual(candidate_hosts_from_cidr("192.0.2.10/32", port=18080), ["192.0.2.10:18080"])

    def test_discover_candidates_through_mock_http_server(self) -> None:
        QuietMockCgiscsiHandler.requests = []
        with TCPServer(("127.0.0.1", 0), QuietMockCgiscsiHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host = f"127.0.0.1:{server.server_address[1]}"
                results = discover_candidates([host], timeout=5, workers=1)
            finally:
                server.shutdown()
                thread.join(timeout=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].vendor, "CANON")
        self.assertEqual(results[0].product, "DR-C225")
        self.assertTrue(any(params.get("c", [""])[0].startswith("12") for params in QuietMockCgiscsiHandler.requests))


class ScanPlanTests(unittest.TestCase):
    def test_duplex_plan_matches_driver_order(self) -> None:
        plan = build_scan_plan(duplex=True, chunk_len=0x10000)
        self.assertEqual(
            [item.name for item in plan[:8]],
            [
                "reserve",
                "object_position_feed",
                "read_prescan_block",
                "set_window_front",
                "set_window_back",
                "define_scan_mode_feed",
                "define_scan_mode_buffer",
                "define_scan_mode_color",
            ],
        )
        scan = next(item for item in plan if item.name == "scan")
        self.assertEqual(scan.cdb.hex(), "1b0000000200")
        self.assertEqual(scan.data_out, b"\x00\x01")
        window = next(item for item in plan if item.name == "set_window_front")
        self.assertIsNotNone(window.data_out)
        desc = window.data_out[8:]  # type: ignore[index]
        self.assertEqual(int.from_bytes(desc[0x0E:0x12], "big"), A4_WIDTH_1200)
        self.assertEqual(int.from_bytes(desc[0x12:0x16], "big"), A4_HEIGHT_1200)

    def test_raw_scan_plan_uses_uncompressed_window(self) -> None:
        plan = build_scan_plan(duplex=False, chunk_len=0x10000, compression=0x00)
        window = next(item for item in plan if item.name == "set_window_front")
        self.assertIsNotNone(window.data_out)
        desc = window.data_out[8:]  # type: ignore[index]
        self.assertEqual(desc[0x20], 0x00)

    def test_scan_plan_sets_compression_arg(self) -> None:
        plan = build_scan_plan(duplex=False, chunk_len=0x10000, compression_arg=2)
        window = next(item for item in plan if item.name == "set_window_front")
        self.assertIsNotNone(window.data_out)
        desc = window.data_out[8:]  # type: ignore[index]
        self.assertEqual(desc[0x21], 0x02)

    def test_jpeg_extraction_and_pdf_assembly(self) -> None:
        frame1 = b"\xff\xd8one\xff\xd9"
        frame2 = b"\xff\xd8two\xff\xd9"
        self.assertEqual(extract_jpegs(b"noise" + frame1 + b"gap" + frame2), [frame1, frame2])

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jpg = tmp_path / "page-001.jpg"
            pdf = tmp_path / "out.pdf"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(jpg, "JPEG")
            jpeg_files_to_pdf([jpg], pdf, rotate_degrees=180)
            pdf_data = pdf.read_bytes()
            self.assertGreater(pdf.stat().st_size, 0)
            self.assertIn(b"/DCTDecode", pdf_data)
            self.assertIn(b"/Rotate 180", pdf_data)

    def test_ocrmypdf_wrapper_options(self) -> None:
        class FakeOcrmypdf:
            def __init__(self) -> None:
                self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

            def ocr(self, *args: object, **kwargs: object) -> None:
                self.calls.append((args, kwargs))
                Path(args[1]).write_bytes(b"%PDF-ocr\n")  # type: ignore[index]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_pdf = tmp_path / "scan.pdf"
            output_pdf = default_ocr_output_pdf(input_pdf)
            tessdata_dir = tmp_path / "tessdata"
            tessdata_dir.mkdir()
            input_pdf.write_bytes(b"%PDF-image\n")
            fake = FakeOcrmypdf()

            self.assertEqual(parse_ocr_languages("deu+eng,fra"), ["deu", "eng", "fra"])
            self.assertEqual(output_pdf.name, "scan-ocr.pdf")
            with patch("scan_to_pdf.importlib.import_module", return_value=fake):
                run_ocrmypdf(input_pdf, output_pdf, language_expr="deu+eng,fra", tessdata_dir=tessdata_dir)

            self.assertTrue(output_pdf.exists())
            call_args, call_kwargs = fake.calls[0]
            self.assertEqual(call_args, (str(input_pdf), str(output_pdf)))
            self.assertEqual(call_kwargs["language"], ["deu", "eng", "fra"])
            self.assertEqual(call_kwargs["clean"], False)
            self.assertEqual(call_kwargs["deskew"], False)
            self.assertEqual(call_kwargs["rotate_pages"], True)
            self.assertEqual(call_kwargs["optimize"], 1)
            self.assertEqual(call_kwargs["output_type"], "pdf")
            self.assertEqual(call_kwargs["progress_bar"], False)

    def test_output_jpeg_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src.jpg"
            dst = tmp_path / "dst.jpg"
            image = Image.new("RGB", (8, 6), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 2, 2), fill="black")
            image.save(src, "JPEG", quality=100)
            write_output_jpeg(src, dst, rotate_degrees=180)

            with Image.open(dst) as rotated:
                self.assertEqual(rotated.size, (8, 6))
                self.assertLess(rotated.getpixel((6, 4))[0], 80)

    def test_output_jpeg_crop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src.jpg"
            dst = tmp_path / "dst.jpg"
            Image.new("RGB", (10, 8), "white").save(src, "JPEG", quality=100)

            write_output_jpeg(src, dst, crop_margin_px=2)

            with Image.open(dst) as cropped:
                self.assertEqual(cropped.size, (6, 4))

    def test_blank_jpeg_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blank = tmp_path / "blank.jpg"
            content = tmp_path / "content.jpg"
            Image.new("RGB", (100, 100), "white").save(blank, "JPEG")
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 20, 80, 35), fill="black")
            image.save(content, "JPEG")

            self.assertTrue(is_blank_jpeg_page(blank)[0])
            self.assertFalse(is_blank_jpeg_page(content)[0])

    def test_raw_file_to_pdf_with_stride(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = tmp_path / "page.raw"
            pdf = tmp_path / "raw.pdf"
            raw.write_bytes(bytes([0, 64, 128, 255, 0xAA, 0xBB]) + bytes([255, 128, 64, 0, 0xCC, 0xDD]))
            raw_file_to_pdf(
                raw_path=raw,
                output_pdf=pdf,
                width=4,
                height=2,
                mode="L",
                stride=6,
            )
            self.assertGreater(pdf.stat().st_size, 0)

    def test_sense_summary(self) -> None:
        summary = sense_summary(bytes.fromhex("f000050000000006000000002600"))
        self.assertIn("key=0x05", summary)
        self.assertIn("asc=0x26", summary)
        self.assertTrue(is_no_more_image_data_sense(bytes.fromhex("f000050000000006000000003a00")))
        self.assertTrue(is_no_more_image_data_sense(bytes.fromhex("f000050000000006000000002c00")))
        self.assertFalse(is_no_more_image_data_sense(bytes.fromhex("f000050000000006000000002600")))

    def test_execute_scan_capture_with_fake_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_jpg = tmp_path / "source.jpg"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_jpg, "JPEG")
            fake = FakeClient(source_jpg.read_bytes())
            out_dir = tmp_path / "capture"
            out_pdf = out_dir / "scan.pdf"

            with redirect_stdout(StringIO()):
                execute_scan_capture(
                    client=fake,  # type: ignore[arg-type]
                    duplex=True,
                    chunk_len=0x10000,
                    output_dir=out_dir,
                    output_pdf=out_pdf,
                    max_chunks=4,
                    max_bytes=0x100000,
                    stop_after_frames=1,
                )

            self.assertTrue(out_pdf.exists())
            self.assertEqual(len(list(out_dir.glob("scan-*.bin"))), 1)
            self.assertEqual(len(list(out_dir.glob("page-*.jpg"))), 1)
            opcodes = [call[0][0] for call in fake.calls]
            self.assertIn(0x1B, opcodes)
            self.assertEqual(opcodes[-3:], [0xD8, 0x31, 0x17])

    def test_execute_raw_scan_capture_with_fake_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_page = bytes([0, 64, 128, 255])
            fake = FakeClient(raw_page)
            out_dir = tmp_path / "raw-capture"
            out_pdf = out_dir / "scan.pdf"

            with redirect_stdout(StringIO()):
                execute_scan_capture(
                    client=fake,  # type: ignore[arg-type]
                    duplex=False,
                    chunk_len=len(raw_page),
                    output_dir=out_dir,
                    output_pdf=out_pdf,
                    max_chunks=1,
                    max_bytes=len(raw_page),
                    stop_after_frames=0,
                    compression=0x00,
                    raw_width=2,
                    raw_height=2,
                    raw_mode="L",
                )

            self.assertTrue(out_pdf.exists())
            self.assertEqual(len(list(out_dir.glob("scan-*.bin"))), 1)
            window_payloads = [call[1] for call in fake.calls if call[0][0] == 0x24]
            self.assertTrue(window_payloads)
            self.assertEqual(window_payloads[0][8 + 0x20], 0x00)  # type: ignore[index]

    def test_execute_scan_capture_stops_on_no_document_sense(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_jpg = tmp_path / "source.jpg"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_jpg, "JPEG")
            fake = FakeNoDocumentAfterOneReadClient(source_jpg.read_bytes())
            out_dir = tmp_path / "capture"

            with redirect_stdout(StringIO()):
                pages = execute_scan_capture(
                    client=fake,  # type: ignore[arg-type]
                    duplex=False,
                    chunk_len=0x10000,
                    output_dir=out_dir,
                    output_pdf=None,
                    max_chunks=10,
                    max_bytes=0x100000,
                    stop_after_frames=2,
                )

            self.assertEqual(len(pages), 1)
            self.assertEqual(fake.image_reads, 2)

    def test_execute_sheet_batch_capture_with_fake_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_jpg = tmp_path / "source.jpg"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_jpg, "JPEG")
            fake = FakeClient(source_jpg.read_bytes())
            out_dir = tmp_path / "batch"
            out_pdf = out_dir / "scan.pdf"

            with redirect_stdout(StringIO()):
                ordered_pages = execute_sheet_batch_capture(
                    client=fake,  # type: ignore[arg-type]
                    sheets=2,
                    duplex=False,
                    chunk_len=0x10000,
                    output_dir=out_dir,
                    output_pdf=out_pdf,
                    max_chunks=2,
                    max_bytes=0x100000,
                    stop_after_frames=1,
                )

            self.assertTrue(out_pdf.exists())
            self.assertEqual([path.name for path in ordered_pages], ["page-001.jpg", "page-002.jpg"])
            self.assertFalse((out_dir / "sheet-01" / "scan.pdf").exists())
            self.assertFalse((out_dir / "sheet-02" / "scan.pdf").exists())
            opcodes = [call[0][0] for call in fake.calls]
            self.assertEqual(opcodes.count(0x1B), 2)

    def test_execute_auto_adf_capture_drops_blank_backs_until_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blank_path = tmp_path / "blank.jpg"
            content_path = tmp_path / "content.jpg"
            Image.new("RGB", (100, 100), "white").save(blank_path, "JPEG")
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 20, 80, 35), fill="black")
            image.save(content_path, "JPEG")
            blank = blank_path.read_bytes()
            content = content_path.read_bytes()
            fake = FakeAutoAdfClient([content + blank, content + content])
            out_dir = tmp_path / "auto"
            out_pdf = out_dir / "scan.pdf"

            with redirect_stdout(StringIO()):
                ordered_pages = execute_auto_adf_capture(
                    client=fake,  # type: ignore[arg-type]
                    chunk_len=0x10000,
                    output_dir=out_dir,
                    output_pdf=out_pdf,
                    max_sheets=5,
                    max_chunks=2,
                    max_bytes=0x100000,
                    stop_after_frames=2,
                )

            self.assertTrue(out_pdf.exists())
            self.assertEqual([path.name for path in ordered_pages], ["page-001.jpg", "page-002.jpg", "page-003.jpg"])
            opcodes = [call[0][0] for call in fake.calls]
            self.assertEqual(opcodes.count(0x1B), 3)

    def test_execute_scan_capture_through_mock_http_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_jpg = tmp_path / "source.jpg"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_jpg, "JPEG")
            QuietMockCgiscsiHandler.jpeg_payload = source_jpg.read_bytes()
            QuietMockCgiscsiHandler.requests = []

            with TCPServer(("127.0.0.1", 0), QuietMockCgiscsiHandler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    host = f"127.0.0.1:{server.server_address[1]}"
                    client = CgiscsiClient(host, timeout=5)
                    out_dir = tmp_path / "http-capture"
                    out_pdf = out_dir / "scan.pdf"
                    with redirect_stdout(StringIO()):
                        execute_scan_capture(
                            client=client,
                            duplex=True,
                            chunk_len=0x10000,
                            output_dir=out_dir,
                            output_pdf=out_pdf,
                            max_chunks=4,
                            max_bytes=0x100000,
                            stop_after_frames=1,
                        )
                finally:
                    server.shutdown()
                    thread.join(timeout=5)

            self.assertTrue(out_pdf.exists())
            bodies = QuietMockCgiscsiHandler.requests
            self.assertTrue(any(params.get("c", [""])[0].startswith("1b") for params in bodies))
            self.assertTrue(any(params.get("c", [""])[0].startswith("28") for params in bodies))


if __name__ == "__main__":
    unittest.main()
