import unittest
from aurora_monitor import fetcher
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


class LegacyTlsRetryTests(unittest.TestCase):
    def test_handshake_failure_triggers_retry(self):
        from urllib.error import URLError

        exc = URLError("[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] sslv3 alert handshake failure (_ssl.c:1007)")
        self.assertTrue(fetcher._needs_legacy_tls_retry(exc))

    def test_small_dh_key_triggers_retry(self):
        from urllib.error import URLError

        exc = URLError("[SSL: DH_KEY_TOO_SMALL] dh key too small (_ssl.c:1007)")
        self.assertTrue(fetcher._needs_legacy_tls_retry(exc))

    def test_certificate_failure_does_not_trigger_retry(self):
        from urllib.error import URLError

        exc = URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1007)")
        self.assertFalse(fetcher._needs_legacy_tls_retry(exc))

    def test_non_ssl_error_does_not_trigger_retry(self):
        from urllib.error import URLError

        exc = URLError("[Errno 111] Connection refused")
        self.assertFalse(fetcher._needs_legacy_tls_retry(exc))

    def test_referer_header_is_set_when_provided(self):
        request = fetcher._build_request("https://example.edu.cn/download.jsp", referer="https://example.edu.cn/notice.htm")
        self.assertEqual(request.get_header("Referer"), "https://example.edu.cn/notice.htm")

    def test_referer_header_absent_by_default(self):
        request = fetcher._build_request("https://example.edu.cn/download.jsp")
        self.assertIsNone(request.get_header("Referer"))
