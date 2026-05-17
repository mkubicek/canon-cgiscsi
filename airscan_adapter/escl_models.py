"""Small eSCL XML helpers for the offline adapter skeleton.

The goal is conservative interoperability, not a complete eSCL implementation.
Only the settings that map cleanly to the current Canon backend plan are
accepted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring as defused_fromstring

NS_PWG = "http://www.pwg.org/schemas/2010/12/sm"
NS_SCAN = "http://schemas.hp.com/imaging/escl/2011/05/03"

ET.register_namespace("pwg", NS_PWG)
ET.register_namespace("scan", NS_SCAN)


class UnsupportedScanSetting(ValueError):
    """Raised when a client asks for a setting the MVP profile does not expose."""


@dataclass(frozen=True)
class ScanRegion:
    width: int = 2480
    height: int = 3508
    x_offset: int = 0
    y_offset: int = 0


@dataclass(frozen=True)
class ScanSettings:
    input_source: str = "Feeder"
    color_mode: str = "Grayscale8"
    document_format: str = "image/jpeg"
    x_resolution: int = 300
    y_resolution: int = 300
    duplex: bool = True
    blank_page_detection: bool = True
    region: ScanRegion = ScanRegion()


def qname(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def _first_text(root: ET.Element, *names: str) -> str | None:
    wanted = set(names)
    for elem in root.iter():
        if _local_name(elem.tag) in wanted and elem.text is not None:
            text = elem.text.strip()
            if text:
                return text
    return None


def _first_int(root: ET.Element, name: str, default: int) -> int:
    text = _first_text(root, name)
    if text is None:
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise UnsupportedScanSetting(f"{name} must be an integer") from exc


def _text_bool(text: str | None, default: bool) -> bool:
    if text is None:
        return default
    normalized = text.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise UnsupportedScanSetting(f"unsupported boolean value {text!r}")


def _sides_to_duplex(text: str | None, default: bool) -> bool:
    if text is None:
        return default
    normalized = text.strip().lower()
    if normalized in {"twosidedlongedge", "twosidedshortedge", "duplex"}:
        return True
    if normalized in {"onesided", "simplex"}:
        return False
    raise UnsupportedScanSetting(f"unsupported Sides value {text!r}")


def _add_text(parent: ET.Element, namespace: str, tag: str, value: str | int) -> ET.Element:
    elem = ET.SubElement(parent, qname(namespace, tag))
    elem.text = str(value)
    return elem


def scanner_capabilities_xml(
    model_name: str = "Canon DR-C225W AirScan",
    uuid: str = "urn:uuid:canon-cgiscsi-airscan",
    admin_uri: str = "http://localhost:8765/admin",
    max_width: int = 2550,
    max_height: int = 4200,
) -> bytes:
    """Return an ADF-only MVP capability document.

    Width and height are expressed in 300 DPI pixels for the initial profile.
    No platen capability is emitted so clients do not offer a misleading
    flatbed source.
    """

    root = ET.Element(qname(NS_SCAN, "ScannerCapabilities"))
    _add_text(root, NS_PWG, "Version", "2.1")
    _add_text(root, NS_PWG, "MakeAndModel", model_name)
    _add_text(root, NS_PWG, "Manufacturer", "Canon")
    _add_text(root, NS_SCAN, "UUID", uuid)
    _add_text(root, NS_SCAN, "AdminURI", admin_uri)

    adf = ET.SubElement(root, qname(NS_SCAN, "Adf"))
    for caps_name in ("AdfSimplexInputCaps", "AdfDuplexInputCaps"):
        caps = ET.SubElement(adf, qname(NS_SCAN, caps_name))
        _add_text(caps, NS_SCAN, "MinWidth", 1)
        _add_text(caps, NS_SCAN, "MaxWidth", max_width)
        _add_text(caps, NS_SCAN, "MinHeight", 1)
        _add_text(caps, NS_SCAN, "MaxHeight", max_height)
        _add_text(caps, NS_SCAN, "MaxScanRegions", 1)

        resolutions = ET.SubElement(caps, qname(NS_SCAN, "SettingProfiles"))
        profile = ET.SubElement(resolutions, qname(NS_SCAN, "SettingProfile"))
        color_modes = ET.SubElement(profile, qname(NS_SCAN, "ColorModes"))
        _add_text(color_modes, NS_SCAN, "ColorMode", "Grayscale8")
        document_formats = ET.SubElement(profile, qname(NS_SCAN, "DocumentFormats"))
        _add_text(document_formats, NS_PWG, "DocumentFormat", "image/jpeg")
        supported_resolutions = ET.SubElement(profile, qname(NS_SCAN, "SupportedResolutions"))
        discrete_resolutions = ET.SubElement(supported_resolutions, qname(NS_SCAN, "DiscreteResolutions"))
        discrete_resolution = ET.SubElement(discrete_resolutions, qname(NS_SCAN, "DiscreteResolution"))
        _add_text(discrete_resolution, NS_SCAN, "XResolution", 300)
        _add_text(discrete_resolution, NS_SCAN, "YResolution", 300)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def scanner_status_xml(
    state: str = "Idle",
    adf_state: str | None = "ScannerAdfLoaded",
    reason: str | None = None,
    jobs: list[dict[str, object]] | None = None,
) -> bytes:
    root = ET.Element(qname(NS_SCAN, "ScannerStatus"))
    _add_text(root, NS_PWG, "Version", "2.1")
    _add_text(root, NS_PWG, "State", state)
    if adf_state:
        _add_text(root, NS_SCAN, "AdfState", adf_state)
    if reason:
        _add_text(root, NS_SCAN, "StateReason", reason)
    if jobs:
        jobs_elem = ET.SubElement(root, qname(NS_SCAN, "Jobs"))
        for job in jobs:
            info = ET.SubElement(jobs_elem, qname(NS_SCAN, "JobInfo"))
            _add_text(info, NS_PWG, "JobUri", str(job.get("uri", "")))
            _add_text(info, NS_PWG, "JobUuid", str(job.get("uuid", "")))
            _add_text(info, NS_PWG, "JobState", str(job.get("state", "Processing")))
            _add_text(info, NS_PWG, "ImagesToTransfer", int(job.get("images_to_transfer", 0)))
            _add_text(info, NS_PWG, "ImagesCompleted", int(job.get("images_completed", 0)))
            reasons = ET.SubElement(info, qname(NS_PWG, "JobStateReasons"))
            _add_text(reasons, NS_PWG, "JobStateReason", str(job.get("reason", "Processing")))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def scan_settings_from_xml(data: bytes | str) -> ScanSettings:
    """Parse the conservative MVP subset of an eSCL ScanSettings document."""

    if isinstance(data, str):
        data = data.encode("utf-8")
    root = defused_fromstring(data)

    input_source = _first_text(root, "InputSource") or "Feeder"
    if input_source not in {"Feeder", "ADF", "Adf"}:
        raise UnsupportedScanSetting(f"unsupported input source {input_source!r}")

    color_mode = _first_text(root, "ColorMode") or "Grayscale8"
    if color_mode != "Grayscale8":
        raise UnsupportedScanSetting(f"unsupported color mode {color_mode!r}")

    document_format = _first_text(root, "DocumentFormat", "DocumentFormatExt") or "image/jpeg"
    if document_format != "image/jpeg":
        raise UnsupportedScanSetting(f"unsupported document format {document_format!r}")

    x_resolution = _first_int(root, "XResolution", 300)
    y_resolution = _first_int(root, "YResolution", 300)
    if (x_resolution, y_resolution) != (300, 300):
        raise UnsupportedScanSetting(
            f"unsupported resolution {x_resolution}x{y_resolution}"
        )

    duplex = _text_bool(_first_text(root, "Duplex"), True)
    duplex = _sides_to_duplex(_first_text(root, "Sides"), duplex)
    blank_page_detection = _text_bool(
        _first_text(root, "BlankPageDetection", "BlankPageDetectionAndRemoval"),
        True,
    )

    region = ScanRegion(
        width=_first_int(root, "Width", ScanRegion.width),
        height=_first_int(root, "Height", ScanRegion.height),
        x_offset=_first_int(root, "XOffset", 0),
        y_offset=_first_int(root, "YOffset", 0),
    )
    if region.width <= 0 or region.height <= 0:
        raise UnsupportedScanSetting("scan region width and height must be positive")
    if region.x_offset < 0 or region.y_offset < 0:
        raise UnsupportedScanSetting("scan region offsets must be non-negative")

    return ScanSettings(
        input_source="Feeder",
        color_mode=color_mode,
        document_format=document_format,
        x_resolution=x_resolution,
        y_resolution=y_resolution,
        duplex=duplex,
        blank_page_detection=blank_page_detection,
        region=region,
    )


def scan_image_info_xml(
    *,
    job_uri: str,
    width: int = 2480,
    height: int = 3508,
    bytes_per_line: int | None = None,
    blank_page_detected: bool = False,
) -> bytes:
    root = ET.Element(qname(NS_SCAN, "ScanImageInfo"))
    _add_text(root, NS_SCAN, "JobUri", job_uri)
    _add_text(root, NS_SCAN, "ActualWidth", width)
    _add_text(root, NS_SCAN, "ActualHeight", height)
    _add_text(root, NS_SCAN, "ActualBytesPerLine", bytes_per_line or width)
    _add_text(root, NS_SCAN, "BlankPageDetected", str(blank_page_detected).lower())
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def error_xml(message: str) -> bytes:
    root = ET.Element(qname(NS_SCAN, "Error"))
    _add_text(root, NS_SCAN, "Message", message)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
