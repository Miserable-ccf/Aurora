import unittest
from aurora_monitor.fetcher import FetchResult


class ConditionalFetchTests(unittest.TestCase):
    def test_not_modified_result_is_explicit(self):
        result = FetchResult("https://example.gov.cn", 304, "", b"", 2, not_modified=True)
        self.assertTrue(result.not_modified)
        self.assertEqual(result.status, 304)


if __name__ == "__main__":
    unittest.main()
