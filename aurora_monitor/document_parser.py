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
        document = fitz.open(stream=body, filetype="pdf")
        text = "\n".join(page.get_text() for page in document).strip()
        return ParsedDocument(text, parser_status="parsed" if text else "empty", warnings=[] if text else ["PDF has no extractable text layer"])
    if "spreadsheet" in normalized_type or "excel" in normalized_type or suffix.endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook  # type: ignore
        except ImportError:
            return ParsedDocument("", parser_status="needs_dependency", warnings=["XLSX parsing requires openpyxl"])
        workbook = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
        rows: list[list[str]] = []
        for sheet in workbook.worksheets:
            for values in sheet.iter_rows(values_only=True):
                row = [str(value).strip() if value is not None else "" for value in values]
                if any(row):
                    rows.append(row)
        text = "\n".join("\t".join(row) for row in rows)
        return ParsedDocument(text, rows=rows)
    return ParsedDocument(body.decode("utf-8", errors="replace"), parser_status="unknown_type", warnings=[f"unsupported content type: {content_type or 'unknown'}"])


def _decode_text(body: bytes, charset: str | None = None) -> str:
    for encoding in (charset, "utf-8", "gb18030", "gbk"):
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")
