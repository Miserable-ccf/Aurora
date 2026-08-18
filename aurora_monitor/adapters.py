from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

from .fetcher import FetchResult, extract_links


@dataclass(frozen=True)
class CandidateLink:
    title: str
    url: str


def is_next_page(candidate: CandidateLink) -> bool:
    """Recognize conservative pagination labels; arbitrary links are never followed."""
    label = re.sub(r"\s+", "", candidate.title).lower()
    return label in {"下一页", "下页", "后一页", "next", "nextpage", ">", ">>"} or "page=" in candidate.url.lower() and label in {"1", "2", "3", "4", "5", "下一页"}


def discover(result: FetchResult, adapter: str) -> list[CandidateLink]:
    if adapter == "generic_html_v1":
        return [CandidateLink(title, url) for title, url in extract_links(result)]
    if adapter == "json_links_v1":
        return _discover_json(result)
    if adapter == "rss_v1":
        return _discover_rss(result)
    raise ValueError(f"unsupported source adapter: {adapter}")


def _discover_json(result: FetchResult) -> list[CandidateLink]:
    if "json" not in result.content_type:
        raise ValueError("json_links_v1 requires an application/json response")
    payload = json.loads(result.body.decode(result.charset or "utf-8"))
    items = _find_items(payload)
    links: list[CandidateLink] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("subject") or "").strip()
        href = str(item.get("url") or item.get("href") or item.get("link") or "").strip()
        if title and href:
            links.append(CandidateLink(title, urljoin(result.url, href)))
    return links


def _discover_rss(result: FetchResult) -> list[CandidateLink]:
    if "xml" not in result.content_type and "rss" not in result.content_type and "atom" not in result.content_type:
        raise ValueError("rss_v1 requires an XML/RSS response")
    root = ElementTree.fromstring(result.body)
    links: list[CandidateLink] = []
    for item in root.iter():
        local_name = item.tag.rsplit("}", 1)[-1].lower()
        if local_name not in {"item", "entry"}:
            continue
        title = _child_text(item, "title")
        href = _child_text(item, "link")
        if not href:
            for child in item:
                if child.tag.rsplit("}", 1)[-1].lower() == "link" and child.attrib.get("href"):
                    href = child.attrib["href"]
                    break
        if title and href:
            links.append(CandidateLink(title, urljoin(result.url, href)))
    return links


def _child_text(parent: ElementTree.Element, name: str) -> str:
    for child in parent:
        if child.tag.rsplit("}", 1)[-1].lower() == name:
            return (child.text or "").strip()
    return ""


def _find_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "list", "results", "news", "articles"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for key in ("data", "result", "payload"):
        value = payload.get(key)
        nested = _find_items(value)
        if nested:
            return nested
    return []
