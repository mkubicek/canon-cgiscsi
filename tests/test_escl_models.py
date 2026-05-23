import unittest
from xml.etree import ElementTree as ET

from airscan_adapter.escl_models import (
    ADF_MAX_HEIGHT,
    ADF_MAX_WIDTH,
    UnsupportedScanSetting,
    scan_settings_from_xml,
    scanner_capabilities_xml,
    scanner_status_xml,
)

SCAN_SETTINGS = """\
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:InputSource>Feeder</pwg:InputSource>
  <scan:DocumentFormat>image/jpeg</scan:DocumentFormat>
  <scan:ColorMode>Grayscale8</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
  <scan:Sides>TwoSidedLongEdge</scan:Sides>
  <scan:BlankPageDetectionAndRemoval>false</scan:BlankPageDetectionAndRemoval>
</scan:ScanSettings>
"""


def scan_settings_with_region(width, height, x_offset=0, y_offset=0):
    region = f"""\
  <scan:ScanRegions>
    <scan:ScanRegion>
      <scan:XOffset>{x_offset}</scan:XOffset>
      <scan:YOffset>{y_offset}</scan:YOffset>
      <scan:Width>{width}</scan:Width>
      <scan:Height>{height}</scan:Height>
    </scan:ScanRegion>
  </scan:ScanRegions>
"""
    return SCAN_SETTINGS.replace("</scan:ScanSettings>", region + "</scan:ScanSettings>")


class EsclModelTests(unittest.TestCase):
    def test_capabilities_are_adf_only_jpeg_grayscale(self):
        xml = scanner_capabilities_xml()
        text = xml.decode("utf-8")

        self.assertIn("AdfSimplexInputCaps", text)
        self.assertIn("AdfDuplexInputCaps", text)
        self.assertIn("image/jpeg", text)
        self.assertIn("Grayscale8", text)
        self.assertNotIn("Platen", text)
        ET.fromstring(xml)

    def test_status_xml_is_well_formed(self):
        xml = scanner_status_xml(state="Idle", adf_state="ScannerAdfLoaded")
        text = xml.decode("utf-8")

        self.assertIn("Idle", text)
        self.assertIn("ScannerAdfLoaded", text)
        ET.fromstring(xml)

    def test_parse_supported_scan_settings(self):
        settings = scan_settings_from_xml(SCAN_SETTINGS)

        self.assertEqual(settings.input_source, "Feeder")
        self.assertEqual(settings.document_format, "image/jpeg")
        self.assertEqual(settings.color_mode, "Grayscale8")
        self.assertEqual(settings.x_resolution, 300)
        self.assertEqual(settings.y_resolution, 300)
        self.assertTrue(settings.duplex)
        self.assertFalse(settings.blank_page_detection)

    def test_reject_unsupported_color_mode(self):
        xml = SCAN_SETTINGS.replace("Grayscale8", "RGB24")

        with self.assertRaises(UnsupportedScanSetting):
            scan_settings_from_xml(xml)

    def test_reject_unsupported_pdf_output(self):
        xml = SCAN_SETTINGS.replace("image/jpeg", "application/pdf")

        with self.assertRaises(UnsupportedScanSetting):
            scan_settings_from_xml(xml)

    def test_reject_unsupported_resolution(self):
        xml = SCAN_SETTINGS.replace("<scan:XResolution>300", "<scan:XResolution>600")

        with self.assertRaises(UnsupportedScanSetting):
            scan_settings_from_xml(xml)

    def test_reject_scan_region_wider_than_advertised_adf(self):
        xml = scan_settings_with_region(ADF_MAX_WIDTH + 1, 3508)

        with self.assertRaises(UnsupportedScanSetting):
            scan_settings_from_xml(xml)

    def test_reject_scan_region_taller_than_advertised_adf(self):
        xml = scan_settings_with_region(2480, ADF_MAX_HEIGHT + 1)

        with self.assertRaises(UnsupportedScanSetting):
            scan_settings_from_xml(xml)

    def test_reject_scan_region_offset_outside_advertised_adf(self):
        xml = scan_settings_with_region(2480, 3508, x_offset=100)

        with self.assertRaises(UnsupportedScanSetting):
            scan_settings_from_xml(xml)


if __name__ == "__main__":
    unittest.main()
