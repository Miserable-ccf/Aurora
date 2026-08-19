from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes
    elapsed_ms: int
    charset: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            title = re.sub(r"\s+", " ", "".join(self._text)).replace("\ufeff", "").strip()
            if title:
                self.links.append((title, self._href))
            self._href = None


def host_allowed(host: str, allowed_domains: list[str]) -> bool:
    """检查主机是否在白名单内；支持 `*.gov.cn` 通配后缀条目。"""
    host = host.lower()
    for item in allowed_domains:
        entry = item.lower().strip()
        if entry.startswith("*."):
            if host.endswith(entry[1:]):
                return True
        else:
            domain = entry.lstrip(".")
            if host == domain or host.endswith("." + domain):
                return True
    return False


def fetch(url: str, allowed_domains: list[str], timeout: int = 15, max_bytes: int = 20 * 1024 * 1024, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http/https sources are allowed")
    host = (parsed.hostname or "").lower()
    if not host_allowed(host, allowed_domains):
        raise ValueError(f"host is not allowlisted: {host}")
    headers = {"User-Agent": "AuroraMonitor/0.1", "Accept": "text/html,application/pdf,application/vnd.ms-excel"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = Request(url, headers=headers)
    started = time.monotonic()
    try:
        response_context = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        if exc.code == 304:
            return FetchResult(url, 304, "", b"", int((time.monotonic() - started) * 1000), not_modified=True)
        raise
    with response_context as response:
        final_host = (urlparse(response.geturl()).hostname or "").lower()
        if not host_allowed(final_host, allowed_domains):
            raise ValueError(f"redirect target is not allowlisted: {final_host}")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("response exceeds size limit")
        status = getattr(response, "status", 200)
        return FetchResult(url, status, response.headers.get_content_type(), body, int((time.monotonic() - started) * 1000), response.headers.get_content_charset(), response.headers.get("ETag"), response.headers.get("Last-Modified"), status == 304)


def extract_links(result: FetchResult) -> list[tuple[str, str]]:
    if result.content_type not in {"text/html", "application/xhtml+xml"}:
        return []
    text = _decode_text(result.body, result.charset)
    parser = LinkParser()
    parser.feed(text)
    return [(title, urljoin(result.url, href)) for title, href in parser.links]


def _decode_text(body: bytes, charset: str | None = None) -> str:
    encodings = [charset, "utf-8", "gb18030", "gbk"]
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")
