import unittest

from aurora_monitor.monitor import _extract_date


class MetadataTests(unittest.TestCase):
    def test_extract_date_from_title(self):
        self.assertEqual(_extract_date("2026年8月16日事业单位招聘", "https://example.gov.cn/a"), "2026-08-16")

    def test_extract_date_returns_none_when_missing(self):
        self.assertIsNone(_extract_date("事业单位招聘公告", "https://example.gov.cn/a"))


if __name__ == "__main__":
    unittest.main()
