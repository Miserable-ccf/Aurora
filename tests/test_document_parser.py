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

    @staticmethod
    def _docx_bytes(paragraphs):
        import io
        import zipfile

        body_xml = "".join(f"<w:p><w:r><w:t>{item}</w:t></w:r></w:p>" for item in paragraphs)
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body_xml}</w:body></w:document>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

    def test_docx_text_is_extractable(self):
        body = self._docx_bytes(["2026年公开招聘公告", "报名时间为9月1日"])
        result = parse_document(body, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "https://example.edu.cn/a.docx")
        self.assertEqual(result.parser_status, "parsed")
        self.assertIn("2026年公开招聘公告", result.text)
        self.assertIn("报名时间为9月1日", result.text)

    def test_mislabeled_docx_is_sniffed_by_magic(self):
        body = self._docx_bytes(["资格复审材料目录"])
        result = parse_document(body, "application/msword", "https://example.edu.cn/a.docx")
        self.assertEqual(result.parser_status, "parsed")
        self.assertIn("资格复审材料目录", result.text)

    def test_zip_container_with_generic_type_prefers_xlsx(self):
        import io

        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append(["岗位", "人数"])
        workbook.active.append(["专任教师", 2])
        buffer = io.BytesIO()
        workbook.save(buffer)
        result = parse_document(buffer.getvalue(), "application/octet-stream", "https://example.edu.cn/download")
        self.assertEqual(result.parser_status, "parsed")
        self.assertEqual(result.rows[0], ["岗位", "人数"])

    def test_binary_body_is_not_decoded_as_text(self):
        body = b"PK\x03\x04\x00\x01\x02garbage\x00" * 8
        result = parse_document(body, "application/x-rar-compressed", "https://example.edu.cn/a.rar")
        self.assertIn(result.parser_status, {"binary_skipped", "unsupported_format"})
        self.assertEqual(result.text, "")


if __name__ == "__main__":
    unittest.main()
