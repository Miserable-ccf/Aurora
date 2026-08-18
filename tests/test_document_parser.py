import unittest

from aurora_monitor.document_parser import parse_document


class DocumentParserTests(unittest.TestCase):
    def test_html_text_is_extractable(self):
        body = "<html><body><h1>招聘公告</h1><p>报名时间</p></body></html>".encode("utf-8")
        result = parse_document(body, "text/html", "https://example.gov.cn/a.html")
        self.assertEqual(result.parser_status, "parsed")
        self.assertIn("招聘公告", result.text)
        self.assertIn("报名时间", result.text)

    def test_unknown_type_is_explicit(self):
        result = parse_document(b"raw", "application/octet-stream", "https://example.gov.cn/a.bin")
        self.assertEqual(result.parser_status, "unknown_type")
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
