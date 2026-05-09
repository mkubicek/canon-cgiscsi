from __future__ import annotations

import argparse
import dataclasses
import importlib
import os
import shutil
import tempfile
import time
from pathlib import Path

import img2pdf
from PIL import Image

from cgiscsi import CgiscsiClient
from commands import (
    cancel_cdb,
    define_scan_mode_buffer_payload,
    define_scan_mode_cdb,
    define_scan_mode_color_payload,
    define_scan_mode_feed_payload,
    object_position_cdb,
    read_cdb,
    read_kind_cdb,
    release_unit_cdb,
    reserve_unit_cdb,
    scan_cdb,
    scan_payload,
    set_window_cdb,
    set_window_payload,
)


A4_WIDTH_1200 = 2480 * 1200 // 300
A4_HEIGHT_1200 = 3508 * 1200 // 300
LETTER_WIDTH_1200 = 2550 * 1200 // 300
LETTER_HEIGHT_1200 = 3300 * 1200 // 300
LEGAL_WIDTH_1200 = 2550 * 1200 // 300
LEGAL_HEIGHT_1200 = 4200 * 1200 // 300
DEFAULT_OCR_LANGUAGE = "deu+eng+fra"
PAPER_SIZES_1200 = {
    "a4": (A4_WIDTH_1200, A4_HEIGHT_1200),
    "letter": (LETTER_WIDTH_1200, LETTER_HEIGHT_1200),
    "legal": (LEGAL_WIDTH_1200, LEGAL_HEIGHT_1200),
}


@dataclasses.dataclass(frozen=True)
class CommandPlanItem:
    name: str
    cdb: bytes
    data_out: bytes | None = None
    data_in_len: int = 0
    pad_cdb_to_12: bool = True


@dataclasses.dataclass(frozen=True)
class PdfOutputOptions:
    ocr: bool = False
    ocr_output_pdf: Path | None = None
    ocr_language: str = DEFAULT_OCR_LANGUAGE
    ocr_clean: bool = False
    ocr_deskew: bool = False
    ocr_rotate_pages: bool = True
    ocr_optimize: int = 1
    ocr_tessdata_dir: Path | None = None


def pdf_rotation_from_degrees(rotate_degrees: int) -> img2pdf.Rotation:
    rotations = {
        0: img2pdf.Rotation["0"],
        90: img2pdf.Rotation["90"],
        180: img2pdf.Rotation["180"],
        270: img2pdf.Rotation["270"],
    }
    try:
        return rotations[rotate_degrees]
    except KeyError as exc:
        raise ValueError("rotate_degrees must be one of: 0, 90, 180, 270") from exc


def jpeg_files_to_pdf(jpeg_paths: list[Path], output_pdf: Path, *, rotate_degrees: int = 0) -> None:
    if not jpeg_paths:
        raise ValueError("no JPEG paths supplied")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.write_bytes(
        img2pdf.convert([str(path) for path in jpeg_paths], rotation=pdf_rotation_from_degrees(rotate_degrees))
    )


def parse_ocr_languages(language_expr: str) -> list[str]:
    languages = [part.strip() for part in language_expr.replace(",", "+").split("+") if part.strip()]
    if not languages:
        raise ValueError("at least one OCR language is required")
    return languages


def default_ocr_output_pdf(input_pdf: Path) -> Path:
    return input_pdf.with_name(f"{input_pdf.stem}-ocr{input_pdf.suffix}")


def run_ocrmypdf(
    input_pdf: Path,
    output_pdf: Path,
    *,
    language_expr: str = DEFAULT_OCR_LANGUAGE,
    clean: bool = False,
    deskew: bool = False,
    rotate_pages: bool = True,
    optimize: int = 1,
    tessdata_dir: Path | None = None,
) -> None:
    if not input_pdf.exists():
        raise FileNotFoundError(input_pdf)
    if input_pdf.resolve() == output_pdf.resolve():
        raise ValueError("OCR output PDF must be different from the image-only input PDF")
    if optimize not in {0, 1, 2, 3}:
        raise ValueError("OCR optimize must be one of: 0, 1, 2, 3")

    languages = parse_ocr_languages(language_expr)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    try:
        ocrmypdf = importlib.import_module("ocrmypdf")
    except ImportError as exc:
        raise RuntimeError(
            "OCR requested but OCRmyPDF is not installed. Run `brew install ocrmypdf tesseract-lang` "
            "for system tools and `uv sync` for the Python package."
        ) from exc

    old_tessdata_prefix = os.environ.get("TESSDATA_PREFIX")
    if tessdata_dir is not None:
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
    try:
        ocrmypdf.ocr(
            str(input_pdf),
            str(output_pdf),
            language=languages,
            clean=clean,
            deskew=deskew,
            rotate_pages=rotate_pages,
            optimize=optimize,
            output_type="pdf",
            progress_bar=False,
        )
    finally:
        if tessdata_dir is not None:
            if old_tessdata_prefix is None:
                os.environ.pop("TESSDATA_PREFIX", None)
            else:
                os.environ["TESSDATA_PREFIX"] = old_tessdata_prefix


def write_jpeg_pdf_output(
    jpeg_paths: list[Path],
    output_pdf: Path,
    *,
    rotate_degrees: int = 0,
    pdf_options: PdfOutputOptions | None = None,
) -> Path:
    options = pdf_options or PdfOutputOptions()
    if not options.ocr:
        jpeg_files_to_pdf(jpeg_paths, output_pdf, rotate_degrees=rotate_degrees)
        return output_pdf

    final_pdf = options.ocr_output_pdf or output_pdf
    with tempfile.TemporaryDirectory(prefix="canon-cgiscsi-pdf-") as tmp:
        image_pdf = Path(tmp) / "image.pdf"
        jpeg_files_to_pdf(jpeg_paths, image_pdf, rotate_degrees=rotate_degrees)
        print(f"ocr_input_pdf={image_pdf}")
        run_ocrmypdf(
            image_pdf,
            final_pdf,
            language_expr=options.ocr_language,
            clean=options.ocr_clean,
            deskew=options.ocr_deskew,
            rotate_pages=options.ocr_rotate_pages,
            optimize=options.ocr_optimize,
            tessdata_dir=options.ocr_tessdata_dir,
        )
    return final_pdf


def write_output_jpeg(src: Path, dst: Path, *, rotate_degrees: int = 0, crop_margin_px: int = 0) -> None:
    if rotate_degrees not in {0, 90, 180, 270}:
        raise ValueError("rotate_degrees must be one of: 0, 90, 180, 270")
    if crop_margin_px < 0:
        raise ValueError("crop_margin_px must be non-negative")
    if rotate_degrees == 0 and crop_margin_px == 0:
        shutil.copy2(src, dst)
        return

    with Image.open(src) as image:
        processed = image.copy()
        try:
            if rotate_degrees:
                rotated = processed.rotate(rotate_degrees, expand=True)
                processed.close()
                processed = rotated
            if crop_margin_px:
                width, height = processed.size
                if crop_margin_px * 2 >= width or crop_margin_px * 2 >= height:
                    raise ValueError("crop_margin_px is too large for image dimensions")
                cropped = processed.crop(
                    (crop_margin_px, crop_margin_px, width - crop_margin_px, height - crop_margin_px)
                )
                processed.close()
                processed = cropped
            save_kwargs: dict[str, object] = {"quality": 95}
            if "dpi" in image.info:
                save_kwargs["dpi"] = image.info["dpi"]
            processed.save(dst, "JPEG", **save_kwargs)
        finally:
            processed.close()


def jpeg_dark_pixel_fraction(
    jpeg_path: Path,
    *,
    pixel_threshold: int = 245,
    margin_ratio: float = 0.03,
) -> float:
    if not 0 <= pixel_threshold <= 255:
        raise ValueError("pixel_threshold must be in the range 0..255")
    if not 0 <= margin_ratio < 0.45:
        raise ValueError("margin_ratio must be in the range 0..0.45")

    with Image.open(jpeg_path) as image:
        gray = image.convert("L")
        width, height = gray.size
        x_margin = int(width * margin_ratio)
        y_margin = int(height * margin_ratio)
        if width - (2 * x_margin) > 0 and height - (2 * y_margin) > 0:
            gray = gray.crop((x_margin, y_margin, width - x_margin, height - y_margin))
        gray.thumbnail((512, 512))
        histogram = gray.histogram()
        dark_pixels = sum(histogram[:pixel_threshold])
        total_pixels = sum(histogram)
    if total_pixels == 0:
        return 0.0
    return dark_pixels / total_pixels


def is_blank_jpeg_page(
    jpeg_path: Path,
    *,
    pixel_threshold: int = 245,
    fraction_threshold: float = 0.01,
) -> tuple[bool, float]:
    fraction = jpeg_dark_pixel_fraction(jpeg_path, pixel_threshold=pixel_threshold)
    return fraction <= fraction_threshold, fraction


def raw_file_to_pdf(
    *,
    raw_path: Path,
    output_pdf: Path,
    width: int,
    height: int,
    mode: str,
    stride: int | None = None,
) -> None:
    mode = mode.upper()
    if mode not in {"1", "L", "RGB"}:
        raise ValueError("raw mode must be one of: 1, L, RGB")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    row_bytes = {"1": (width + 7) // 8, "L": width, "RGB": width * 3}[mode]
    stride = row_bytes if stride is None else stride
    if stride < row_bytes:
        raise ValueError("stride must be greater than or equal to the packed row length")

    data = raw_path.read_bytes()
    expected_len = stride * height
    if len(data) < expected_len:
        raise ValueError(f"raw file too short: got {len(data)} bytes, need {expected_len}")

    if stride == row_bytes:
        image_data = data[:expected_len]
    else:
        packed = bytearray(row_bytes * height)
        for row in range(height):
            src_start = row * stride
            dst_start = row * row_bytes
            packed[dst_start : dst_start + row_bytes] = data[src_start : src_start + row_bytes]
        image_data = bytes(packed)

    image = Image.frombytes(mode, (width, height), image_data)
    try:
        converted = image.convert("RGB")
        try:
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            converted.save(output_pdf, "PDF")
        finally:
            converted.close()
    finally:
        image.close()


def extract_jpegs(stream: bytes) -> list[bytes]:
    frames: list[bytes] = []
    offset = 0
    while True:
        start = stream.find(b"\xff\xd8", offset)
        if start < 0:
            return frames
        end = stream.find(b"\xff\xd9", start + 2)
        if end < 0:
            return frames
        end += 2
        frames.append(stream[start:end])
        offset = end


def sense_summary(sense: bytes) -> str:
    if len(sense) < 14:
        return sense.hex()
    key = sense[2] & 0x0F
    asc = sense[12]
    ascq = sense[13]
    return f"key=0x{key:02x} asc=0x{asc:02x} ascq=0x{ascq:02x} raw={sense.hex()}"


def sense_key_asc(sense: bytes) -> tuple[int, int, int] | None:
    if len(sense) < 14:
        return None
    return sense[2] & 0x0F, sense[12], sense[13]


def is_no_more_image_data_sense(sense: bytes) -> bool:
    parsed = sense_key_asc(sense)
    if parsed is None:
        return False
    key, asc, _ascq = parsed
    return key == 0x05 and asc in {0x2C, 0x3A}


def build_scan_plan(
    *,
    duplex: bool,
    chunk_len: int,
    compression: int = 0x80,
    compression_arg: int = 3,
    dpi_x: int = 300,
    dpi_y: int = 300,
    ulx_1200: int = 0,
    uly_1200: int = 0,
    width_1200: int = A4_WIDTH_1200,
    height_1200: int = A4_HEIGHT_1200,
) -> list[CommandPlanItem]:
    front_window = set_window_payload(
        window_id=0,
        compression=compression,
        dpi_x=dpi_x,
        dpi_y=dpi_y,
        ulx_1200=ulx_1200,
        uly_1200=uly_1200,
        width_1200=width_1200,
        height_1200=height_1200,
        compression_arg=compression_arg,
    )
    back_window = set_window_payload(
        window_id=1,
        compression=compression,
        dpi_x=dpi_x,
        dpi_y=dpi_y,
        ulx_1200=ulx_1200,
        uly_1200=uly_1200,
        width_1200=width_1200,
        height_1200=height_1200,
        compression_arg=compression_arg,
    )
    windows = scan_payload(duplex=duplex)

    commands: list[CommandPlanItem] = [
        CommandPlanItem("reserve", reserve_unit_cdb()),
        CommandPlanItem("object_position_feed", object_position_cdb(feed=True), pad_cdb_to_12=False),
        CommandPlanItem("read_prescan_block", read_kind_cdb(6, length=0x80), data_in_len=0x80),
        CommandPlanItem("set_window_front", set_window_cdb(len(front_window)), front_window),
    ]
    if duplex:
        commands.append(CommandPlanItem("set_window_back", set_window_cdb(len(back_window)), back_window))
    commands.extend(
        [
            CommandPlanItem("define_scan_mode_feed", define_scan_mode_cdb(), define_scan_mode_feed_payload()),
            CommandPlanItem(
                "define_scan_mode_buffer",
                define_scan_mode_cdb(),
                define_scan_mode_buffer_payload(duplex=duplex),
            ),
            CommandPlanItem("define_scan_mode_color", define_scan_mode_cdb(), define_scan_mode_color_payload()),
            CommandPlanItem("scan", scan_cdb(len(windows)), windows),
            CommandPlanItem("read_image_chunk", read_cdb(length=chunk_len), data_in_len=chunk_len),
            CommandPlanItem("object_position_discharge", object_position_cdb(feed=False), pad_cdb_to_12=False),
            CommandPlanItem("release", release_unit_cdb()),
        ]
    )
    return commands


def print_scan_plan(
    *,
    duplex: bool,
    chunk_len: int,
    compression: int = 0x80,
    compression_arg: int = 3,
    dpi_x: int = 300,
    dpi_y: int = 300,
    ulx_1200: int = 0,
    uly_1200: int = 0,
    width_1200: int = A4_WIDTH_1200,
    height_1200: int = A4_HEIGHT_1200,
) -> None:
    for item in build_scan_plan(
        duplex=duplex,
        chunk_len=chunk_len,
        compression=compression,
        compression_arg=compression_arg,
        dpi_x=dpi_x,
        dpi_y=dpi_y,
        ulx_1200=ulx_1200,
        uly_1200=uly_1200,
        width_1200=width_1200,
        height_1200=height_1200,
    ):
        data_out_hex = item.data_out.hex() if item.data_out is not None else "-"
        print(
            f"{item.name}: cdb={item.cdb.hex()} data_out={data_out_hex} "
            f"data_in_len={item.data_in_len} pad_cdb_to_12={item.pad_cdb_to_12}"
        )


def best_effort_cleanup(client: CgiscsiClient, *, sent_feed: bool, sent_reserve: bool) -> None:
    for name, cdb, kwargs in [
        ("cancel", cancel_cdb(), {"data_in_len": 0}),
        ("object_position_discharge", object_position_cdb(feed=False), {"data_in_len": 0, "pad_cdb_to_12": False}),
        ("release", release_unit_cdb(), {"data_in_len": 0}),
    ]:
        if name == "object_position_discharge" and not sent_feed:
            continue
        if name == "release" and not sent_reserve:
            continue
        try:
            print(f"cleanup={name}")
            client.execute(cdb, **kwargs)
        except RuntimeError as exc:
            print(f"cleanup_failed={name} error={exc}")


def execute_scan_capture(
    *,
    client: CgiscsiClient,
    duplex: bool,
    chunk_len: int,
    output_dir: Path,
    output_pdf: Path | None,
    max_chunks: int,
    max_bytes: int,
    stop_after_frames: int,
    compression: int = 0x80,
    compression_arg: int = 3,
    raw_width: int | None = None,
    raw_height: int | None = None,
    raw_mode: str = "L",
    raw_stride: int | None = None,
    rotate_degrees: int = 0,
    crop_margin_px: int = 0,
    pdf_options: PdfOutputOptions | None = None,
    dpi_x: int = 300,
    dpi_y: int = 300,
    ulx_1200: int = 0,
    uly_1200: int = 0,
    width_1200: int = A4_WIDTH_1200,
    height_1200: int = A4_HEIGHT_1200,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"scan-{time.strftime('%Y%m%d-%H%M%S')}.bin"
    image_stream = bytearray()
    sent_reserve = False
    sent_feed = False
    reached_scan = False
    jpeg_paths: list[Path] = []

    try:
        for item in build_scan_plan(
            duplex=duplex,
            chunk_len=chunk_len,
            compression=compression,
            compression_arg=compression_arg,
            dpi_x=dpi_x,
            dpi_y=dpi_y,
            ulx_1200=ulx_1200,
            uly_1200=uly_1200,
            width_1200=width_1200,
            height_1200=height_1200,
        ):
            if item.name == "read_image_chunk":
                break
            print(f"send={item.name}")
            resp = client.execute(
                item.cdb,
                data_out=item.data_out,
                data_in_len=item.data_in_len,
                pad_cdb_to_12=item.pad_cdb_to_12,
            )
            print(f"  http={resp.http_status} data_len={len(resp.data)} sense={sense_summary(resp.sense)}")
            sent_reserve = sent_reserve or item.name == "reserve"
            sent_feed = sent_feed or item.name == "object_position_feed"
            reached_scan = reached_scan or item.name == "scan"

        if not reached_scan:
            raise RuntimeError("scan command was not reached")

        for index in range(max_chunks):
            if len(image_stream) >= max_bytes:
                print(f"stopping=max_bytes bytes={len(image_stream)}")
                break
            request_len = min(chunk_len, max_bytes - len(image_stream))
            print(f"read_image_chunk={index} len={request_len}")
            resp = client.execute(
                read_cdb(length=request_len),
                data_in_len=request_len,
            )
            image_stream.extend(resp.data)
            print(f"  http={resp.http_status} data_len={len(resp.data)} sense={sense_summary(resp.sense)}")
            if stop_after_frames and len(extract_jpegs(bytes(image_stream))) >= stop_after_frames:
                print(f"stopping=jpeg_frames frames={stop_after_frames}")
                break
            if is_no_more_image_data_sense(resp.sense):
                print(f"stopping=sense_no_more_image_data sense={sense_summary(resp.sense)}")
                break
            if len(resp.data) < request_len:
                print("stopping=short_read")
                break
    finally:
        raw_path.write_bytes(image_stream)
        print(f"wrote_raw={raw_path} bytes={len(image_stream)}")
        frames = extract_jpegs(bytes(image_stream))
        for index, frame in enumerate(frames, start=1):
            path = output_dir / f"page-{index:03d}.jpg"
            path.write_bytes(frame)
            jpeg_paths.append(path)
            print(f"wrote_jpeg={path} bytes={len(frame)}")
        if output_pdf and jpeg_paths:
            pdf_pages = jpeg_paths
            if crop_margin_px:
                pages_dir = output_dir / "pages"
                if pages_dir.exists():
                    shutil.rmtree(pages_dir)
                pages_dir.mkdir()
                pdf_pages = []
                for index, src in enumerate(jpeg_paths, start=1):
                    dst = pages_dir / f"page-{index:03d}.jpg"
                    write_output_jpeg(src, dst, crop_margin_px=crop_margin_px)
                    pdf_pages.append(dst)
                    print(f"prepared_page={dst} source={src} crop_margin_px={crop_margin_px}")
            written_pdf = write_jpeg_pdf_output(
                pdf_pages,
                output_pdf,
                rotate_degrees=rotate_degrees,
                pdf_options=pdf_options,
            )
            print(f"wrote_pdf={written_pdf}")
        elif output_pdf and raw_width and raw_height:
            options = pdf_options or PdfOutputOptions()
            if options.ocr:
                final_pdf = options.ocr_output_pdf or output_pdf
                with tempfile.TemporaryDirectory(prefix="canon-cgiscsi-pdf-") as tmp:
                    image_pdf = Path(tmp) / "image.pdf"
                    raw_file_to_pdf(
                        raw_path=raw_path,
                        output_pdf=image_pdf,
                        width=raw_width,
                        height=raw_height,
                        mode=raw_mode,
                        stride=raw_stride,
                    )
                    print(f"ocr_input_pdf={image_pdf}")
                    run_ocrmypdf(
                        image_pdf,
                        final_pdf,
                        language_expr=options.ocr_language,
                        clean=options.ocr_clean,
                        deskew=options.ocr_deskew,
                        rotate_pages=options.ocr_rotate_pages,
                        optimize=options.ocr_optimize,
                        tessdata_dir=options.ocr_tessdata_dir,
                    )
                print(f"wrote_pdf={final_pdf}")
            else:
                raw_file_to_pdf(
                    raw_path=raw_path,
                    output_pdf=output_pdf,
                    width=raw_width,
                    height=raw_height,
                    mode=raw_mode,
                    stride=raw_stride,
                )
                print(f"wrote_pdf={output_pdf}")
        best_effort_cleanup(client, sent_feed=sent_feed, sent_reserve=sent_reserve)
    return jpeg_paths


def execute_sheet_batch_capture(
    *,
    client: CgiscsiClient,
    sheets: int,
    duplex: bool,
    chunk_len: int,
    output_dir: Path,
    output_pdf: Path,
    max_chunks: int,
    max_bytes: int,
    stop_after_frames: int,
    compression: int = 0x80,
    compression_arg: int = 3,
    rotate_degrees: int = 0,
    crop_margin_px: int = 0,
    pdf_options: PdfOutputOptions | None = None,
    dpi_x: int = 300,
    dpi_y: int = 300,
    ulx_1200: int = 0,
    uly_1200: int = 0,
    width_1200: int = A4_WIDTH_1200,
    height_1200: int = A4_HEIGHT_1200,
) -> list[Path]:
    if sheets <= 0:
        raise ValueError("sheets must be positive")
    if compression != 0x80:
        raise ValueError("batch PDF assembly currently expects JPEG scan output")

    output_dir.mkdir(parents=True, exist_ok=True)
    collected: list[Path] = []
    for sheet in range(1, sheets + 1):
        sheet_dir = output_dir / f"sheet-{sheet:02d}"
        print(f"batch_sheet={sheet}")
        pages = execute_scan_capture(
            client=client,
            duplex=duplex,
            chunk_len=chunk_len,
            output_dir=sheet_dir,
            output_pdf=None,
            max_chunks=max_chunks,
            max_bytes=max_bytes,
            stop_after_frames=stop_after_frames,
            compression=compression,
            compression_arg=compression_arg,
            dpi_x=dpi_x,
            dpi_y=dpi_y,
            ulx_1200=ulx_1200,
            uly_1200=uly_1200,
            width_1200=width_1200,
            height_1200=height_1200,
        )
        if not pages:
            raise RuntimeError(f"no JPEG frames captured for sheet {sheet}")
        collected.extend(pages)

    pages_dir = output_dir / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir()
    ordered_pages: list[Path] = []
    for index, src in enumerate(collected, start=1):
        dst = pages_dir / f"page-{index:03d}.jpg"
        if crop_margin_px:
            write_output_jpeg(src, dst, crop_margin_px=crop_margin_px)
        else:
            shutil.copy2(src, dst)
        ordered_pages.append(dst)
        print(f"ordered_page={dst} source={src}")
    written_pdf = write_jpeg_pdf_output(
        ordered_pages,
        output_pdf,
        rotate_degrees=rotate_degrees,
        pdf_options=pdf_options,
    )
    print(f"wrote_pdf={written_pdf}")
    return ordered_pages


def execute_auto_adf_capture(
    *,
    client: CgiscsiClient,
    chunk_len: int,
    output_dir: Path,
    output_pdf: Path,
    max_sheets: int,
    max_chunks: int,
    max_bytes: int,
    stop_after_frames: int,
    drop_blank_pages: bool = True,
    blank_pixel_threshold: int = 245,
    blank_fraction_threshold: float = 0.01,
    compression: int = 0x80,
    compression_arg: int = 3,
    rotate_degrees: int = 0,
    crop_margin_px: int = 0,
    pdf_options: PdfOutputOptions | None = None,
    dpi_x: int = 300,
    dpi_y: int = 300,
    ulx_1200: int = 0,
    uly_1200: int = 0,
    width_1200: int = A4_WIDTH_1200,
    height_1200: int = A4_HEIGHT_1200,
) -> list[Path]:
    if max_sheets <= 0:
        raise ValueError("max_sheets must be positive")
    if compression != 0x80:
        raise ValueError("automatic ADF PDF assembly currently expects JPEG scan output")

    output_dir.mkdir(parents=True, exist_ok=True)
    captured_pages: list[Path] = []
    for sheet in range(1, max_sheets + 1):
        sheet_dir = output_dir / f"sheet-{sheet:02d}"
        print(f"auto_sheet={sheet}")
        pages = execute_scan_capture(
            client=client,
            duplex=True,
            chunk_len=chunk_len,
            output_dir=sheet_dir,
            output_pdf=None,
            max_chunks=max_chunks,
            max_bytes=max_bytes,
            stop_after_frames=stop_after_frames,
            compression=compression,
            compression_arg=compression_arg,
            dpi_x=dpi_x,
            dpi_y=dpi_y,
            ulx_1200=ulx_1200,
            uly_1200=uly_1200,
            width_1200=width_1200,
            height_1200=height_1200,
        )
        if not pages:
            print(f"stopping=adf_empty sheet={sheet}")
            break
        captured_pages.extend(pages)

    if not captured_pages:
        raise RuntimeError("no JPEG frames captured before ADF empty")

    pages_dir = output_dir / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir()

    ordered_pages: list[Path] = []
    for src in captured_pages:
        keep = True
        dark_fraction: float | None = None
        if drop_blank_pages:
            blank, dark_fraction = is_blank_jpeg_page(
                src,
                pixel_threshold=blank_pixel_threshold,
                fraction_threshold=blank_fraction_threshold,
            )
            keep = not blank
        if dark_fraction is None:
            print(f"page_candidate={src} action=keep")
        else:
            action = "keep" if keep else "drop_blank"
            print(f"page_candidate={src} dark_fraction={dark_fraction:.6f} action={action}")
        if not keep:
            continue
        dst = pages_dir / f"page-{len(ordered_pages) + 1:03d}.jpg"
        if crop_margin_px:
            write_output_jpeg(src, dst, crop_margin_px=crop_margin_px)
        else:
            shutil.copy2(src, dst)
        ordered_pages.append(dst)
        print(f"ordered_page={dst} source={src}")

    if not ordered_pages:
        raise RuntimeError("all captured JPEG frames were classified as blank")

    written_pdf = write_jpeg_pdf_output(
        ordered_pages,
        output_pdf,
        rotate_degrees=rotate_degrees,
        pdf_options=pdf_options,
    )
    print(f"wrote_pdf={written_pdf}")
    return ordered_pages


def main() -> int:
    parser = argparse.ArgumentParser(description="ADF scan skeleton and PDF helper")
    parser.add_argument(
        "--host",
        default=os.environ.get("CANON_CGISCSI_HOST"),
        help="scanner hostname or host:port; can also be set with CANON_CGISCSI_HOST",
    )
    parser.add_argument("--duplex", action="store_true")
    parser.add_argument("--sheets", type=lambda s: int(s, 0), default=1)
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="repeat duplex sheet scans until the ADF appears empty, then assemble one PDF",
    )
    parser.add_argument("--max-sheets", type=lambda s: int(s, 0), default=100)
    parser.add_argument("--chunk-len", type=lambda s: int(s, 0), default=0x10000)
    parser.add_argument(
        "--image-format",
        choices=["jpeg", "raw"],
        default="jpeg",
        help="SET WINDOW compression mode for scan capture",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--execute-plan",
        action="store_true",
        help="issue verified setup/cleanup commands; does not send SCAN unless --experimental-scan is also set",
    )
    parser.add_argument(
        "--experimental-scan",
        action="store_true",
        help="with --execute-plan, also send SCAN and read image chunks; not yet live-validated",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    parser.add_argument("--output-pdf", type=Path)
    parser.add_argument(
        "--keep-intermediates",
        action="store_true",
        help="keep raw streams, extracted JPEGs, and ordered page copies for debugging; default leaves only the final PDF",
    )
    parser.add_argument("--max-chunks", type=lambda s: int(s, 0), default=64)
    parser.add_argument("--max-bytes", type=lambda s: int(s, 0), default=64 * 1024 * 1024)
    parser.add_argument(
        "--pdf-jpeg-quality",
        type=lambda s: int(s, 0),
        help=(
            "deprecated; JPEG PDFs now embed scanner JPEGs without re-encoding, "
            "so use --scanner-compression-arg to tune capture size"
        ),
    )
    parser.add_argument(
        "--scanner-compression-arg",
        type=lambda s: int(s, 0),
        default=3,
        help="Canon SET WINDOW JPEG compression argument; default 3 matches the observed driver path",
    )
    parser.add_argument("--keep-blank-pages", action="store_true")
    parser.add_argument("--blank-pixel-threshold", type=lambda s: int(s, 0), default=245)
    parser.add_argument("--blank-fraction-threshold", type=float, default=0.01)
    parser.add_argument(
        "--rotate-degrees",
        type=lambda s: int(s, 0),
        choices=[0, 90, 180, 270],
        default=180,
        help="rotate kept JPEG pages before final PDF assembly; default corrects observed DR-C225W feed orientation",
    )
    parser.add_argument(
        "--crop-margin-px",
        type=lambda s: int(s, 0),
        default=0,
        help="optional symmetric JPEG border crop before PDF assembly; disables exact JPEG passthrough for cropped pages",
    )
    parser.add_argument("--paper", choices=sorted(PAPER_SIZES_1200), help="paper preset; defaults to a4")
    parser.add_argument(
        "--page-size",
        choices=sorted(PAPER_SIZES_1200),
        default="a4",
        help="paper preset alias kept for compatibility; defaults to a4",
    )
    parser.add_argument("--dpi-x", type=lambda s: int(s, 0), default=300)
    parser.add_argument("--dpi-y", type=lambda s: int(s, 0), default=300)
    parser.add_argument("--ulx-1200", type=lambda s: int(s, 0), default=0)
    parser.add_argument("--uly-1200", type=lambda s: int(s, 0), default=0)
    parser.add_argument("--width-1200", type=lambda s: int(s, 0))
    parser.add_argument("--height-1200", type=lambda s: int(s, 0))
    parser.add_argument(
        "--stop-after-frames",
        type=lambda s: int(s, 0),
        help="stop after this many complete JPEG frames; default is 2 for duplex and 1 for simplex; use 0 to disable",
    )
    parser.add_argument("--jpeg-to-pdf", nargs="+", type=Path, help="JPEG files followed by output PDF path")
    parser.add_argument("--raw-to-pdf", nargs=2, type=Path, metavar=("RAW", "PDF"))
    parser.add_argument("--raw-width", type=lambda s: int(s, 0))
    parser.add_argument("--raw-height", type=lambda s: int(s, 0))
    parser.add_argument("--raw-mode", choices=["1", "L", "RGB"], default="L")
    parser.add_argument("--raw-stride", type=lambda s: int(s, 0))
    parser.add_argument(
        "--ocr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="create a compact searchable PDF with OCRmyPDF by default; use --no-ocr for image-only PDF output",
    )
    parser.add_argument(
        "--ocr-output-pdf",
        type=Path,
        help="optional searchable PDF output path; by default --output-pdf is the final searchable PDF",
    )
    parser.add_argument(
        "--ocr-language",
        default=DEFAULT_OCR_LANGUAGE,
        help=f"Tesseract language expression for OCRmyPDF; default {DEFAULT_OCR_LANGUAGE}",
    )
    parser.add_argument("--ocr-optimize", type=lambda s: int(s, 0), choices=[0, 1, 2, 3], default=1)
    parser.add_argument(
        "--ocr-tessdata-dir",
        type=Path,
        help="optional tessdata directory to expose to Tesseract during OCR",
    )
    parser.add_argument(
        "--ocr-clean",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="enable OCRmyPDF --clean / unpaper cleanup; off by default because it increases office-document PDF size",
    )
    parser.add_argument(
        "--ocr-deskew",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="enable OCRmyPDF --deskew; off by default because it transcodes images and increases PDF size",
    )
    parser.add_argument(
        "--ocr-rotate-pages",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="allow OCRmyPDF page rotation analysis; on by default",
    )
    args = parser.parse_args()
    if args.crop_margin_px < 0:
        parser.error("--crop-margin-px must be non-negative")
    if args.ocr_output_pdf and not args.ocr:
        parser.error("--ocr-output-pdf requires OCR; remove --no-ocr")
    compression = 0x80 if args.image_format == "jpeg" else 0x00
    if args.paper and args.page_size != "a4" and args.paper != args.page_size:
        parser.error("--paper and --page-size must agree when both are supplied")
    paper = args.paper or args.page_size
    page_width_1200, page_height_1200 = PAPER_SIZES_1200[paper]
    width_1200 = args.width_1200 if args.width_1200 is not None else page_width_1200
    height_1200 = args.height_1200 if args.height_1200 is not None else page_height_1200
    pdf_options = PdfOutputOptions(
        ocr=args.ocr,
        ocr_output_pdf=args.ocr_output_pdf,
        ocr_language=args.ocr_language,
        ocr_clean=args.ocr_clean,
        ocr_deskew=args.ocr_deskew,
        ocr_rotate_pages=args.ocr_rotate_pages,
        ocr_optimize=args.ocr_optimize,
        ocr_tessdata_dir=args.ocr_tessdata_dir,
    )

    def scan_output_pdf() -> Path:
        return args.output_pdf or args.ocr_output_pdf or (args.output_dir / "scan.pdf")

    def with_capture_work_dir(callback):
        if args.keep_intermediates:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            return callback(args.output_dir)
        with tempfile.TemporaryDirectory(prefix="canon-cgiscsi-capture-") as tmp:
            return callback(Path(tmp))

    if args.jpeg_to_pdf:
        if len(args.jpeg_to_pdf) < 2:
            parser.error("--jpeg-to-pdf requires one or more JPEGs and an output PDF")
        *jpeg_paths, output_pdf = args.jpeg_to_pdf
        if args.crop_margin_px:
            with tempfile.TemporaryDirectory(prefix="canon-cgiscsi-jpeg-") as tmp:
                prepared_paths: list[Path] = []
                tmp_path = Path(tmp)
                for index, src in enumerate(jpeg_paths, start=1):
                    dst = tmp_path / f"page-{index:03d}.jpg"
                    write_output_jpeg(src, dst, crop_margin_px=args.crop_margin_px)
                    prepared_paths.append(dst)
                written_pdf = write_jpeg_pdf_output(
                    prepared_paths,
                    output_pdf,
                    rotate_degrees=args.rotate_degrees,
                    pdf_options=pdf_options,
                )
        else:
            written_pdf = write_jpeg_pdf_output(
                jpeg_paths,
                output_pdf,
                rotate_degrees=args.rotate_degrees,
                pdf_options=pdf_options,
            )
        print(f"wrote {written_pdf}")
        return 0

    if args.raw_to_pdf:
        if args.raw_width is None or args.raw_height is None:
            parser.error("--raw-to-pdf requires --raw-width and --raw-height")
        raw_path, output_pdf = args.raw_to_pdf
        if pdf_options.ocr:
            final_pdf = pdf_options.ocr_output_pdf or output_pdf
            with tempfile.TemporaryDirectory(prefix="canon-cgiscsi-pdf-") as tmp:
                image_pdf = Path(tmp) / "image.pdf"
                raw_file_to_pdf(
                    raw_path=raw_path,
                    output_pdf=image_pdf,
                    width=args.raw_width,
                    height=args.raw_height,
                    mode=args.raw_mode,
                    stride=args.raw_stride,
                )
                print(f"ocr_input_pdf={image_pdf}")
                run_ocrmypdf(
                    image_pdf,
                    final_pdf,
                    language_expr=pdf_options.ocr_language,
                    clean=pdf_options.ocr_clean,
                    deskew=pdf_options.ocr_deskew,
                    rotate_pages=pdf_options.ocr_rotate_pages,
                    optimize=pdf_options.ocr_optimize,
                    tessdata_dir=pdf_options.ocr_tessdata_dir,
                )
            print(f"wrote {final_pdf}")
        else:
            raw_file_to_pdf(
                raw_path=raw_path,
                output_pdf=output_pdf,
                width=args.raw_width,
                height=args.raw_height,
                mode=args.raw_mode,
                stride=args.raw_stride,
            )
            print(f"wrote {output_pdf}")
        return 0

    if args.execute_plan and not args.host:
        parser.error("--host is required with --execute-plan, or set CANON_CGISCSI_HOST")

    plan_duplex = args.duplex or args.scan_all
    print_scan_plan(
        duplex=plan_duplex,
        chunk_len=args.chunk_len,
        compression=compression,
        compression_arg=args.scanner_compression_arg,
        dpi_x=args.dpi_x,
        dpi_y=args.dpi_y,
        ulx_1200=args.ulx_1200,
        uly_1200=args.uly_1200,
        width_1200=width_1200,
        height_1200=height_1200,
    )

    if not args.execute_plan:
        print("dry_run=true")
        return 0

    client = CgiscsiClient(args.host, timeout=args.timeout)
    if args.experimental_scan:
        stop_after_frames = args.stop_after_frames if args.stop_after_frames is not None else (2 if plan_duplex else 1)
        if args.scan_all:
            output_pdf = scan_output_pdf()
            with_capture_work_dir(
                lambda work_dir: execute_auto_adf_capture(
                    client=client,
                    chunk_len=args.chunk_len,
                    output_dir=work_dir,
                    output_pdf=output_pdf,
                    max_sheets=args.max_sheets,
                    max_chunks=args.max_chunks,
                    max_bytes=args.max_bytes,
                    stop_after_frames=stop_after_frames,
                    drop_blank_pages=not args.keep_blank_pages,
                    blank_pixel_threshold=args.blank_pixel_threshold,
                    blank_fraction_threshold=args.blank_fraction_threshold,
                    compression=compression,
                    compression_arg=args.scanner_compression_arg,
                    rotate_degrees=args.rotate_degrees,
                    crop_margin_px=args.crop_margin_px,
                    pdf_options=pdf_options,
                    dpi_x=args.dpi_x,
                    dpi_y=args.dpi_y,
                    ulx_1200=args.ulx_1200,
                    uly_1200=args.uly_1200,
                    width_1200=width_1200,
                    height_1200=height_1200,
                )
            )
            return 0
        if args.sheets > 1:
            output_pdf = scan_output_pdf()
            with_capture_work_dir(
                lambda work_dir: execute_sheet_batch_capture(
                    client=client,
                    sheets=args.sheets,
                    duplex=plan_duplex,
                    chunk_len=args.chunk_len,
                    output_dir=work_dir,
                    output_pdf=output_pdf,
                    max_chunks=args.max_chunks,
                    max_bytes=args.max_bytes,
                    stop_after_frames=stop_after_frames,
                    compression=compression,
                    compression_arg=args.scanner_compression_arg,
                    rotate_degrees=args.rotate_degrees,
                    crop_margin_px=args.crop_margin_px,
                    pdf_options=pdf_options,
                    dpi_x=args.dpi_x,
                    dpi_y=args.dpi_y,
                    ulx_1200=args.ulx_1200,
                    uly_1200=args.uly_1200,
                    width_1200=width_1200,
                    height_1200=height_1200,
                )
            )
            return 0
        output_pdf = scan_output_pdf()
        with_capture_work_dir(
            lambda work_dir: execute_scan_capture(
                client=client,
                duplex=plan_duplex,
                chunk_len=args.chunk_len,
                output_dir=work_dir,
                output_pdf=output_pdf,
                max_chunks=args.max_chunks,
                max_bytes=args.max_bytes,
                stop_after_frames=stop_after_frames,
                compression=compression,
                compression_arg=args.scanner_compression_arg,
                raw_width=args.raw_width,
                raw_height=args.raw_height,
                raw_mode=args.raw_mode,
                raw_stride=args.raw_stride,
                rotate_degrees=args.rotate_degrees,
                crop_margin_px=args.crop_margin_px,
                pdf_options=pdf_options,
                dpi_x=args.dpi_x,
                dpi_y=args.dpi_y,
                ulx_1200=args.ulx_1200,
                uly_1200=args.uly_1200,
                width_1200=width_1200,
                height_1200=height_1200,
            )
        )
        return 0

    sent_feed = False
    sent_reserve = False
    try:
        for item in build_scan_plan(
            duplex=plan_duplex,
            chunk_len=args.chunk_len,
            compression=compression,
            compression_arg=args.scanner_compression_arg,
            dpi_x=args.dpi_x,
            dpi_y=args.dpi_y,
            ulx_1200=args.ulx_1200,
            uly_1200=args.uly_1200,
            width_1200=width_1200,
            height_1200=height_1200,
        ):
            if item.name in {"scan", "read_image_chunk"} and not args.experimental_scan:
                print("stopping_before_unverified_scan=true")
                break
            if item.name in {"object_position_discharge", "release"} and not args.experimental_scan:
                continue
            print(f"send={item.name}")
            resp = client.execute(
                item.cdb,
                data_out=item.data_out,
                data_in_len=item.data_in_len,
                pad_cdb_to_12=item.pad_cdb_to_12,
            )
            print(f"  http={resp.http_status} data_len={len(resp.data)} sense={resp.sense.hex()}")
            sent_feed = sent_feed or item.name == "object_position_feed"
            sent_reserve = sent_reserve or item.name == "reserve"
    finally:
        if sent_feed and not args.experimental_scan:
            print("cleanup=object_position_discharge")
            client.execute(object_position_cdb(feed=False), data_in_len=0, pad_cdb_to_12=False)
        if sent_reserve and not args.experimental_scan:
            print("cleanup=release")
            client.execute(release_unit_cdb(), data_in_len=0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
