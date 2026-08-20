from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class FilterDecision:
    decision: str
    matched_terms: list[str]
    normalized_title: str


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").replace("\ufeff", "")
    value = re.sub(r"\s+", "", value)
    return value.strip().lower()


def decide(title: str, include: list[str], exclude: list[str], workflow: list[str], process: list[str] | None = None) -> FilterDecision:
    """按关键词判定公告类型。

    优先级：流程词（补充公告/成绩公布/资格复审等招聘流程性通知）命中即为 noise，
    即使标题同时含招聘关键词——岗位信息以原始招聘公告为准，流程通知默认过滤。
    """
    normalized = normalize_text(title)
    include_hits = [term for term in include if normalize_text(term) and normalize_text(term) in normalized]
    exclude_hits = [term for term in exclude if normalize_text(term) and normalize_text(term) in normalized]
    workflow_hits = [term for term in workflow if normalize_text(term) and normalize_text(term) in normalized]
    process_hits = [term for term in (process or []) if normalize_text(term) and normalize_text(term) in normalized]
    if process_hits:
        return FilterDecision("noise", process_hits, normalized)
    if exclude_hits and not (include_hits or workflow_hits):
        return FilterDecision("noise", exclude_hits, normalized)
    if exclude_hits:
        return FilterDecision("needs_review", sorted(set(include_hits + exclude_hits + workflow_hits)), normalized)
    if include_hits:
        return FilterDecision("candidate", sorted(set(include_hits + workflow_hits)), normalized)
    if workflow_hits:
        return FilterDecision("needs_review", workflow_hits, normalized)
    return FilterDecision("noise", [], normalized)
