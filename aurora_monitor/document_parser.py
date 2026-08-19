from __future__ import annotations

import html as html_entities
import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

ZIP_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF"
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    rows: list[list[str]] = field(default_factory=list)
    parser_status: str = "parsed"
    warnings: list[str] = field(default_factory=list)
    sheets: list[tuple[str, list[list[str]]]] = field(default_factory=list)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def parse_document(body: bytes, content_type: str, url: str = "", charset: str | None = None) -> ParsedDocument:
    normalized_type = (content_type or "").lower()
    suffix = urlparse(url).path.lower()
    if normalized_type in {"text/html", "application/xhtml+xml"} or suffix.endswith((".html", ".htm")):
        parser = _TextParser()
        parser.feed(_decode_text(body, charset))
        return ParsedDocument("\n".join(parser.parts))
    if normalized_type == "application/pdf" or suffix.endswith(".pdf") or body.startswith(PDF_MAGIC):
        return _parse_pdf(body)
    if "spreadsheet" in normalized_type or "excel" in normalized_type or suffix.endswith((".xlsx", ".xlsm", ".xls")):
        return _parse_xlsx(body)
    if "wordprocessingml" in normalized_type or suffix.endswith(".docx"):
        return _parse_docx(body)
    if normalized_type == "application/msword" or suffix.endswith(".doc"):
        if body.startswith(ZIP_MAGIC):
            return _parse_docx(body, note="服务器标记为 msword，实际为 OOXML 压缩包")
        return _parse_ole2(body)
    # 服务端未给出有效类型时，按文件魔数嗅探（ZIP 容器多为 xlsx/docx）。
    if body.startswith(ZIP_MAGIC):
        return _parse_zip_container(body, content_type)
    if body.startswith(OLE2_MAGIC):
        return _parse_ole2(body)
    if _looks_binary(body):
        return ParsedDocument("", parser_status="binary_skipped", warnings=[f"二进制附件未解析为文本: {content_type or 'unknown'}"])
    return ParsedDocument(body.decode("utf-8", errors="replace"), parser_status="unknown_type", warnings=[f"unsupported content type: {content_type or 'unknown'}"])


def _parse_pdf(body: bytes) -> ParsedDocument:
    try:
        import fitz  # type: ignore
    except ImportError:
        return ParsedDocument("", parser_status="needs_dependency", warnings=["PDF parsing requires PyMuPDF (fitz)"])
    try:
        document = fitz.open(stream=body, filetype="pdf")
        text = "\n".join(page.get_text() for page in document).strip()
    except Exception as exc:
        return ParsedDocument("", parser_status="error", warnings=[f"PDF 解析失败：{exc}"])
    return ParsedDocument(text, parser_status="parsed" if text else "empty", warnings=[] if text else ["PDF has no extractable text layer"])


def _parse_xlsx(body: bytes, note: str = "") -> ParsedDocument:
    warnings = [note] if note else []
    try:
        from openpyxl import load_workbook  # type: ignore
    except ImportError:
        return ParsedDocument("", parser_status="needs_dependency", warnings=warnings + ["XLSX parsing requires openpyxl"])
    try:
        workbook = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:
        return _parse_xls(body, f"openpyxl 无法解析，尝试 xlrd（{exc}）")
    sheets: list[tuple[str, list[list[str]]]] = []
    rows: list[list[str]] = []
    for sheet in workbook.worksheets:
        sheet_rows: list[list[str]] = []
        for values in sheet.iter_rows(values_only=True):
            row = [str(value).strip() if value is not None else "" for value in values]
            if any(row):
                sheet_rows.append(row)
        if sheet_rows:
            sheets.append((sheet.title, sheet_rows))
            rows.extend(sheet_rows)
    text = "\n".join("\t".join(row) for row in rows)
    return ParsedDocument(text, rows=rows, sheets=sheets, warnings=warnings)


def _parse_docx(body: bytes, note: str = "") -> ParsedDocument:
    warnings = [note] if note else []
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            if "word/document.xml" not in archive.namelist():
                return ParsedDocument("", parser_status="unsupported_format", warnings=warnings + ["ZIP 包缺少 word/document.xml，不是 DOCX"])
            xml = archive.read("word/document.xml").decode("utf-8", "ignore")
    except Exception as exc:
        return ParsedDocument("", parser_status="error", warnings=warnings + [f"DOCX 解析失败：{exc}"])
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = html_entities.unescape(text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return ParsedDocument(text, parser_status="parsed" if text else "empty", warnings=warnings)


def _parse_zip_container(body: bytes, content_type: str) -> ParsedDocument:
    spreadsheet = _parse_xlsx(body, note=f"按魔数识别为 ZIP 容器（声明类型: {content_type or 'unknown'}）")
    if spreadsheet.parser_status in {"parsed", "empty"} and (spreadsheet.rows or spreadsheet.text):
        return spreadsheet
    word = _parse_docx(body)
    if word.parser_status in {"parsed", "empty"} and word.text:
        return ParsedDocument(word.text, parser_status=word.parser_status, warnings=[f"按魔数识别为 DOCX（声明类型: {content_type or 'unknown'}）"])
    return ParsedDocument("", parser_status="unsupported_format", warnings=[f"ZIP 容器无法解析为 xlsx/docx（声明类型: {content_type or 'unknown'}）"])


def _parse_ole2(body: bytes) -> ParsedDocument:
    spreadsheet = _parse_xls(body)
    if spreadsheet.parser_status in {"parsed", "empty"} and (spreadsheet.rows or spreadsheet.text):
        return ParsedDocument(spreadsheet.text, rows=spreadsheet.rows, sheets=spreadsheet.sheets, parser_status=spreadsheet.parser_status, warnings=["按魔数识别为 OLE2 复合文档（xls）"] + spreadsheet.warnings)
    return ParsedDocument("", parser_status="unsupported_format", warnings=["OLE2 复合文档（旧版 .doc/.xls）暂不支持文本提取，请下载附件查看"])


def _looks_binary(body: bytes) -> bool:
    sample = body[:4096]
    return bool(sample) and b"\x00" in sample


def _parse_xls(body: bytes, note: str = "") -> ParsedDocument:
    try:
        import xlrd  # type: ignore
    except ImportError:
        return ParsedDocument("", parser_status="needs_dependency", warnings=["XLS parsing requires xlrd", note])
    try:
        book = xlrd.open_workbook(file_contents=body)
    except Exception as exc:
        return ParsedDocument("", parser_status="error", warnings=[f"XLS 解析失败：{exc}", note])
    sheets: list[tuple[str, list[list[str]]]] = []
    rows: list[list[str]] = []
    for sheet in book.sheets():
        sheet_rows: list[list[str]] = []
        for row_index in range(sheet.nrows):
            row = []
            for value in sheet.row_values(row_index):
                if isinstance(value, float) and value.is_integer():
                    row.append(str(int(value)))
                else:
                    row.append(str(value).strip())
            if any(row):
                sheet_rows.append(row)
        if sheet_rows:
            sheets.append((sheet.name, sheet_rows))
            rows.extend(sheet_rows)
    text = "\n".join("\t".join(row) for row in rows)
    return ParsedDocument(text, rows=rows, sheets=sheets, warnings=[note] if not rows else [])


def _decode_text(body: bytes, charset: str | None = None) -> str:
    for encoding in (charset, "utf-8", "gb18030", "gbk"):
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")
