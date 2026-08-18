from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse


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
    if normalized_type == "application/pdf" or suffix.endswith(".pdf"):
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
    if "spreadsheet" in normalized_type or "excel" in normalized_type or suffix.endswith((".xlsx", ".xlsm", ".xls")):
        try:
            from openpyxl import load_workbook  # type: ignore
        except ImportError:
            return ParsedDocument("", parser_status="needs_dependency", warnings=["XLSX parsing requires openpyxl"])
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
        return ParsedDocument(text, rows=rows, sheets=sheets)
    return ParsedDocument(body.decode("utf-8", errors="replace"), parser_status="unknown_type", warnings=[f"unsupported content type: {content_type or 'unknown'}"])


def _parse_xls(body: bytes, note: str) -> ParsedDocument:
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
