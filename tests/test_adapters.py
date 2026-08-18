import json
import unittest

from aurora_monitor.adapters import discover
from aurora_monitor.fetcher import FetchResult


class AdapterTests(unittest.TestCase):
    def test_json_links_adapter(self):
        body = json.dumps({"data": {"list": [{"title": "招聘公告", "url": "/notice/1"}]}}).encode()
        result = FetchResult("https://example.gov.cn/api", 200, "application/json", body, 1, "utf-8")
        links = discover(result, "json_links_v1")
        self.assertEqual((links[0].title, links[0].url), ("招聘公告", "https://example.gov.cn/notice/1"))

    def test_unknown_adapter_is_rejected(self):
        result = FetchResult("https://example.gov.cn", 200, "text/html", b"", 1)
        with self.assertRaises(ValueError):
            discover(result, "unknown")

    def test_rss_adapter_supports_item_and_atom_entry(self):
        body = "<rss><channel><item><title>招聘公告</title><link>/notice/1</link></item></channel></rss>".encode()
        result = FetchResult("https://example.gov.cn/feed.xml", 200, "application/rss+xml", body, 1)
        links = discover(result, "rss_v1")
        self.assertEqual(links[0].url, "https://example.gov.cn/notice/1")


if __name__ == "__main__":
    unittest.main()
