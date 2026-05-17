import unittest

from airscan_adapter.mock_canon_backend import MockCanonBackend, ScannedPage
from airscan_adapter.server_skeleton import (
    AirscanJobManager,
    JobExhausted,
    JobState,
    ScannerBusy,
)

SCAN_SETTINGS = """\
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:InputSource>Feeder</pwg:InputSource>
  <scan:DocumentFormat>image/jpeg</scan:DocumentFormat>
  <scan:ColorMode>Grayscale8</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
  <scan:Duplex>true</scan:Duplex>
</scan:ScanSettings>
"""


class AirscanJobTests(unittest.TestCase):
    def test_next_document_returns_nonblank_pages_in_order(self):
        backend = MockCanonBackend(
            pages=[
                ScannedPage(1),
                ScannedPage(2, is_blank=True),
                ScannedPage(3),
            ]
        )
        manager = AirscanJobManager(backend)
        job = manager.create_job(SCAN_SETTINGS)

        self.assertEqual(manager.wait_for_job(job.job_id), JobState.COMPLETED)
        self.assertEqual(manager.next_document(job.job_id).page_number, 1)
        self.assertEqual(manager.next_document(job.job_id).page_number, 3)
        with self.assertRaises(JobExhausted):
            manager.next_document(job.job_id)

    def test_single_active_job_rejects_concurrent_scan(self):
        backend = MockCanonBackend(pages=[ScannedPage(1), ScannedPage(2)], delay_seconds=0.2)
        manager = AirscanJobManager(backend)
        job = manager.create_job(SCAN_SETTINGS)

        with self.assertRaises(ScannerBusy):
            manager.create_job(SCAN_SETTINGS)

        manager.delete_job(job.job_id)

    def test_delete_job_cancels_active_job(self):
        backend = MockCanonBackend(pages=[ScannedPage(1), ScannedPage(2)], delay_seconds=0.2)
        manager = AirscanJobManager(backend)
        job = manager.create_job(SCAN_SETTINGS)

        manager.delete_job(job.job_id)

        self.assertIn(manager.get_job(job.job_id).state, {JobState.CANCELED, JobState.COMPLETED})


if __name__ == "__main__":
    unittest.main()

