import unittest
from aurora_monitor.fetcher import host_allowed, FetchResult


class ConditionalFetchTests(unittest.TestCase):
    def test_not_modified_result_is_explicit(self):
        result = FetchResult("https://example.gov.cn", 304, "", b"", 2, not_modified=True)
        self.assertTrue(result.not_modified)
        self.assertEqual(result.status, 304)


if __name__ == "__main__":
    unittest.main()


class HostAllowedTests(unittest.TestCase):
    def test_exact_and_subdomain_match(self):
        self.assertTrue(host_allowed("sqhrss.suqian.gov.cn", ["sqhrss.suqian.gov.cn"]))
        self.assertTrue(host_allowed("a.sqhrss.suqian.gov.cn", ["sqhrss.suqian.gov.cn"]))
        self.assertFalse(host_allowed("www.shuyang.gov.cn", ["sqhrss.suqian.gov.cn"]))

    def test_wildcard_suffix_entry(self):
        allowed = ["sqhrss.suqian.gov.cn", "*.gov.cn"]
        self.assertTrue(host_allowed("www.shuyang.gov.cn", allowed))
        self.assertTrue(host_allowed("jshrss.jiangsu.gov.cn", allowed))
        self.assertFalse(host_allowed("gov.cn.evil.com", allowed))
        self.assertFalse(host_allowed("example.com", allowed))
