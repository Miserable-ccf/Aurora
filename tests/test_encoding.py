import unittest

from aurora_monitor.fetcher import FetchResult, extract_links


class EncodingTests(unittest.TestCase):
    def test_gb18030_titles_are_decoded_for_filtering(self):
        body = '<a href="/a">事业单位招聘公告</a>'.encode("gb18030")
        result = FetchResult("https://example.gov.cn", 200, "text/html", body, 1, "gb18030")
        self.assertEqual(extract_links(result)[0][0], "事业单位招聘公告")

    def test_bom_is_removed_from_title(self):
        body = '<a href="/a">\ufeff事业单位招聘公告</a>'.encode("utf-8")
        result = FetchResult("https://example.gov.cn", 200, "text/html", body, 1, "utf-8")
        self.assertEqual(extract_links(result)[0][0], "事业单位招聘公告")


if __name__ == "__main__":
    unittest.main()
