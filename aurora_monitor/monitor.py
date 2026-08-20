from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from .db import Database, _loads
from .adapters import CandidateLink, discover, is_next_page
from .document_parser import parse_document
from .fetcher import fetch
from .filtering import decide, normalize_text
from .positions import extract_positions, upsert_positions


GROUP_TO_SCOPES = {
    "jiangsu_province_hrss": {"civil_service", "public_institution"},
    "jiangsu_province_recruitment": {"public_institution"},
    "jiangsu_city_hrss": {"civil_service", "public_institution"},
    "jiangsu_city_recruitment": {"public_institution"},
    "jiangsu_public_college": {"public_college"},
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Monitor:
    def __init__(self, database: Database, timeout: int = 15) -> None:
        self.db = database
        self.timeout = timeout

    def run_once(self, limit: int | None = None) -> dict[str, int]:
        stats = {"sources": 0, "changed": 0, "unchanged": 0, "candidate": 0, "needs_review": 0, "noise": 0, "detail_fetched": 0, "detail_failed": 0, "failed": 0, "notifications": 0}
        for source in self.db.due_sources()[:limit]:
            stats["sources"] += 1
            try:
                counts = self._check_source(source)
                for key, value in counts.items():
                    stats[key] += value
            except Exception as exc:
                stats["failed"] += 1
                self._record_check(source["id"], "failed", error_message=str(exc))
                retry_delay = min(3600, 300 * (2 ** min(int(source["consecutive_failures"]), 3)))
                self._schedule(source["id"], retry_delay)
        return stats

    def run_forever(self, interval: int = 60, limit: int | None = None, after_cycle: Callable[[dict[str, int]], None] | None = None) -> None:
        if interval < 5:
            raise ValueError("poll interval must be at least 5 seconds")
        while True:
            stats = self.run_once(limit)
            if after_cycle:
                after_cycle(stats)
            time.sleep(interval)

    def _check_source(self, source) -> dict[str, int]:
        allowed_domains = _loads(source["allowed_domains"])
        result = fetch(source["entry_url"], allowed_domains, timeout=self.timeout, etag=source["etag"], last_modified=source["last_modified"])
        if result.not_modified:
            self._record_check(source["id"], "unchanged", result)
            retry_counts = self._retry_failed_details(source, allowed_domains)
            self._schedule(source["id"], source["check_interval_sec"])
            return {"unchanged": 1, "changed": 0, "candidate": 0, "needs_review": 0, "noise": 0, **retry_counts}
        digest = hashlib.sha256(result.body).hexdigest()
        links: list[CandidateLink] | None = None
        if int(source["max_pages"] or 1) > 1:
            links = self._discover_pages(source, result)
            fingerprint = "\n".join(f"{candidate.title}\t{candidate.url}" for candidate in links).encode("utf-8")
            digest = hashlib.sha256(result.body + b"\n" + fingerprint).hexdigest()
        if digest == source["last_content_sha256"]:
            self._record_check(source["id"], "unchanged", result, digest)
            retry_counts = self._retry_failed_details(source, allowed_domains)
            self._schedule(source["id"], source["check_interval_sec"])
            return {"unchanged": 1, "changed": 0, "candidate": 0, "needs_review": 0, "noise": 0, **retry_counts}
        policy = self.db.active_policies(source["keyword_policy_id"], source["keyword_policy_version"])
        counts = {"unchanged": 0, "changed": 1, "candidate": 0, "needs_review": 0, "noise": 0, "detail_fetched": 0, "detail_failed": 0, "notifications": 0}
        links = links if links is not None else self._discover_pages(source, result)
        for candidate in links:
            title, url = candidate.title, candidate.url
            decision = decide(title, _loads(policy["include_any"]), _loads(policy["exclude_any"]), _loads(policy["workflow_terms"]), _loads(policy["process_terms"]))
            counts[decision.decision] += 1
            notice_id = hashlib.sha256(f"{source['id']}::{url}".encode()).hexdigest()
            is_new_notice = self._upsert_notice(notice_id, source, title, url, decision, _extract_date(title, url))
            evidence_digest: str | None = None
            is_new_evidence = False
            if decision.decision != "noise":
                try:
                    detail = fetch(url, allowed_domains, timeout=self.timeout)
                    evidence_digest, is_new_evidence = self._store_evidence(notice_id, detail)
                    counts["detail_fetched"] += 1 + self._fetch_attachments(notice_id, detail, allowed_domains)
                except Exception:
                    counts["detail_failed"] += 1
                    self._mark_detail_failed(notice_id)
                if is_new_notice and is_new_evidence:
                    counts["notifications"] += self._queue_notifications(notice_id, source, title, "new_notice")
                elif is_new_evidence and evidence_digest:
                    event_key = f"content_change:{evidence_digest[:16]}"
                    counts["notifications"] += self._queue_notifications(notice_id, source, title, event_key, "content_change")
        self._record_check(source["id"], "changed", result, digest)
        self._update_source(source["id"], digest, result)
        self._schedule(source["id"], source["check_interval_sec"])
        return counts

    def _discover_pages(self, source, first_result) -> list[CandidateLink]:
        links = discover(first_result, source["adapter"])
        max_pages = int(source["max_pages"] or 1)
        if max_pages <= 1 or source["adapter"] != "generic_html_v1":
            return links
        seen_pages = {source["entry_url"]}
        pages_checked = 1
        pending = [candidate for candidate in links if is_next_page(candidate)]
        while pending and pages_checked < max_pages:
            page = pending.pop(0)
            if page.url in seen_pages:
                continue
            page_result = fetch(page.url, _loads(source["allowed_domains"]), timeout=self.timeout)
            seen_pages.add(page.url)
            pages_checked += 1
            page_links = discover(page_result, source["adapter"])
            links.extend(candidate for candidate in page_links if not is_next_page(candidate))
            pending.extend(candidate for candidate in page_links if is_next_page(candidate) and candidate.url not in seen_pages)
        return [candidate for candidate in links if not is_next_page(candidate)]

    def _upsert_notice(self, notice_id, source, title, url, decision, published_at: str | None = None) -> bool:
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT 1 FROM notice WHERE source_id = ? AND url = ?", (source["id"], url)).fetchone()
            conn.execute(
                """INSERT INTO notice(id, source_id, title, normalized_title, url, published_at, decision, matched_terms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_id, url) DO UPDATE SET title=excluded.title,
                   normalized_title=excluded.normalized_title, decision=excluded.decision,
                   published_at=COALESCE(excluded.published_at, notice.published_at),
                   matched_terms=excluded.matched_terms, last_seen_at=CURRENT_TIMESTAMP""",
                (notice_id, source["id"], title, decision.normalized_title, url, published_at, decision.decision, json.dumps(decision.matched_terms, ensure_ascii=False)),
            )
        return existing is None

    def _queue_notifications(self, notice_id, source, title, event_key: str, event_type: str | None = None) -> int:
        profile_event = event_type or event_key
        scopes = GROUP_TO_SCOPES.get(source["source_group"], set())
        rows = self.db.connection.execute("SELECT * FROM monitor_profile WHERE enabled = 1").fetchall()
        queued = 0
        for profile in rows:
            if not scopes.intersection(_loads(profile["scope_types"])):
                continue
            if not self._profile_matches(profile, source, title, profile_event):
                continue
            with self.db.transaction() as conn:
                cursor = conn.execute("INSERT OR IGNORE INTO notification(profile_id, notice_id, event_type) VALUES (?, ?, ?)", (profile["id"], notice_id, event_key))
                queued += cursor.rowcount
        return queued

    @staticmethod
    def _profile_matches(profile, source, title: str, event_type: str) -> bool:
        regions = _loads(profile["region_codes"])
        source_region = source["region_code"]
        if regions and not any(source_region == region or source_region.startswith(region + "-") for region in regions):
            return False
        institutions = _loads(profile["institution_ids"])
        if institutions and source["institution_id"] not in institutions:
            return False
        normalized_title = normalize_text(title)
        include = [normalize_text(value) for value in _loads(profile["include_keywords"]) if normalize_text(value)]
        exclude = [normalize_text(value) for value in _loads(profile["exclude_keywords"]) if normalize_text(value)]
        if include and not any(value in normalized_title for value in include):
            return False
        if any(value in normalized_title for value in exclude):
            return False
        return event_type in _loads(profile["event_types"])

    def _store_evidence(self, notice_id: str, result) -> tuple[str, bool]:
        digest = hashlib.sha256(result.body).hexdigest()
        evidence_id = hashlib.sha256(f"{notice_id}::{digest}".encode()).hexdigest()
        object_path = self.db.store_object(digest, result.body)
        parsed = parse_document(result.body, result.content_type, result.url, result.charset)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO evidence_version(
                   id, notice_id, source_url, content_sha256, content_type, body, object_path,
                   extracted_text, parser_status, parser_warnings)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (evidence_id, notice_id, result.url, digest, result.content_type, None, object_path, parsed.text, parsed.parser_status, json.dumps(parsed.warnings, ensure_ascii=False)),
            )
            conn.execute("UPDATE notice SET detail_status='fetched', detail_failures=0, next_detail_retry_at=NULL, last_seen_at=CURRENT_TIMESTAMP WHERE id=?", (notice_id,))
        if cursor.rowcount > 0:
            try:
                records = extract_positions(parsed, result.url)
                if records:
                    upsert_positions(self.db, notice_id, evidence_id, records)
            except Exception:
                pass
        return digest, cursor.rowcount > 0

    def _fetch_attachments(self, notice_id: str, detail, allowed_domains: list[str]) -> int:
        if detail.content_type not in {"text/html", "application/xhtml+xml"}:
            return 0
        fetched = 0
        candidates: list[CandidateLink] = []
        subnotices: list[CandidateLink] = []
        seen_urls = {detail.url}
        for candidate in discover(detail, "generic_html_v1"):
            if candidate.url in seen_urls:
                continue
            if _looks_like_attachment(candidate.title, candidate.url):
                seen_urls.add(candidate.url)
                candidates.append(candidate)
            elif _looks_like_subnotice(candidate.url):
                seen_urls.add(candidate.url)
                subnotices.append(candidate)
        for candidate in candidates[:10]:
            fetched += self._fetch_attachment_or_subpage(notice_id, candidate, allowed_domains, referer=detail.url)
        # 嵌套子公告页面（如市级统一公告挂各县区公告链接）：跟进一层并在其中挖附件。
        for candidate in subnotices[:10]:
            fetched += self._fetch_attachment_or_subpage(notice_id, candidate, allowed_domains, referer=detail.url)
        return fetched

    def _fetch_attachment_or_subpage(self, notice_id: str, candidate: CandidateLink, allowed_domains: list[str], referer: str | None = None) -> int:
        """抓取附件候选；若返回的是网页（岗位表入口子页面），保存证据并在其中再找一层附件。

        部分 CMS（如万博 download.jsp）对附件下载做防盗链校验，缺少 Referer 会返回
        验证页/空白页，因此统一携带来源公告页 URL 作为 Referer。
        """
        try:
            result = fetch(candidate.url, allowed_domains, timeout=self.timeout, referer=referer)
        except Exception:
            return 0
        if result.content_type in {"text/html", "application/xhtml+xml"} and not _url_has_file_extension(candidate.url):
            self._store_evidence(notice_id, result)
            fetched = 0
            for inner in discover(result, "generic_html_v1"):
                if _looks_like_attachment(inner.title, inner.url):
                    fetched += self._fetch_attachment_file(notice_id, inner.url, allowed_domains, referer=result.url)
            return fetched
        _, inserted = self._store_evidence(notice_id, result)
        return int(inserted)

    def _fetch_attachment_file(self, notice_id: str, url: str, allowed_domains: list[str], referer: str | None = None) -> int:
        try:
            attachment = fetch(url, allowed_domains, timeout=self.timeout, referer=referer)
        except Exception:
            return 0
        _, inserted = self._store_evidence(notice_id, attachment)
        return int(inserted)

    def fetch_attachments_for_notice(self, notice_id: str) -> int:
        """重新抓取指定公告详情页并补抓附件，用于修复历史漏抓。"""
        row = self.db.connection.execute(
            """SELECT n.url, s.allowed_domains FROM notice n
               JOIN source s ON s.id = n.source_id WHERE n.id = ?""",
            (notice_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"notice not found: {notice_id}")
        allowed_domains = _loads(row["allowed_domains"])
        detail = fetch(row["url"], allowed_domains, timeout=self.timeout)
        if detail.content_type not in {"text/html", "application/xhtml+xml"}:
            return 0
        return self._fetch_attachments(notice_id, detail, allowed_domains)

    def _mark_detail_failed(self, notice_id: str) -> None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT detail_failures FROM notice WHERE id=?", (notice_id,)).fetchone()
            failures = int(row["detail_failures"] if row else 0) + 1
            delay = min(3600, 300 * (2 ** min(failures - 1, 3)))
            conn.execute(
                """UPDATE notice SET detail_status='failed', detail_failures=?,
                   next_detail_retry_at=datetime('now', ? || ' seconds') WHERE id=?""",
                (failures, delay, notice_id),
            )

    def _retry_failed_details(self, source, allowed_domains: list[str]) -> dict[str, int]:
        rows = self.db.connection.execute(
            """SELECT * FROM notice WHERE source_id = ? AND decision != 'noise'
               AND detail_status = 'failed'
               AND (next_detail_retry_at IS NULL OR next_detail_retry_at <= CURRENT_TIMESTAMP)
               ORDER BY next_detail_retry_at LIMIT 20""",
            (source["id"],),
        ).fetchall()
        fetched = failed = notifications = 0
        for notice in rows:
            try:
                detail = fetch(notice["url"], allowed_domains, timeout=self.timeout)
                digest, inserted = self._store_evidence(notice["id"], detail)
                fetched += 1 + self._fetch_attachments(notice["id"], detail, allowed_domains)
                if inserted:
                    has_notification = self.db.connection.execute("SELECT 1 FROM notification WHERE notice_id=? LIMIT 1", (notice["id"],)).fetchone()
                    if not has_notification:
                        notifications += self._queue_notifications(notice["id"], source, notice["title"], "new_notice")
                    else:
                        notifications += self._queue_notifications(notice["id"], source, notice["title"], f"content_change:{digest[:16]}", "content_change")
            except Exception:
                self._mark_detail_failed(notice["id"])
                failed += 1
        return {"detail_fetched": fetched, "detail_failed": failed, "notifications": notifications}

    def _record_check(self, source_id, status, result=None, digest=None, error_message=None) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO source_check(source_id, status, http_status, content_type, response_bytes, response_ms, content_sha256, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (source_id, status, getattr(result, "status", None), getattr(result, "content_type", None), len(result.body) if result else None, getattr(result, "elapsed_ms", None), digest, error_message))
            if status in {"changed", "unchanged", "completed"}:
                conn.execute("UPDATE source SET consecutive_failures = 0, status = CASE WHEN status = 'degraded' THEN 'enabled' ELSE status END, last_success_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (source_id,))
            elif status in {"failed", "blocked"}:
                conn.execute("""UPDATE source SET consecutive_failures = consecutive_failures + 1,
                   status = CASE WHEN consecutive_failures + 1 >= 3 THEN 'degraded' ELSE status END,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (source_id,))

    def _update_source(self, source_id, digest, result=None) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE source SET last_content_sha256 = ?, etag = COALESCE(?, etag), last_modified = COALESCE(?, last_modified), last_success_at = CURRENT_TIMESTAMP, consecutive_failures = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (digest, getattr(result, "etag", None), getattr(result, "last_modified", None), source_id))

    def _schedule(self, source_id, interval: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE source SET next_check_at = datetime('now', ? || ' seconds') WHERE id = ?", (interval, source_id))


def _extract_date(title: str, url: str) -> str | None:
    text = f"{title} {url}"
    match = re.search(r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})日?", text)
    if not match:
        match = re.search(r"(20\d{2})(\d{2})(\d{2})", text)
    if not match:
        return None
    year, month, day = (int(value) for value in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _looks_like_subnotice(url: str) -> bool:
    """官方子公告页：gov.cn 域名下以 .shtml/.html/.htm 结尾的页面（排除栏目列表页）。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host.endswith(".gov.cn"):
        return False
    path = parsed.path.lower()
    if not path.endswith((".shtml", ".html", ".htm")):
        return False
    name = path.rsplit("/", 1)[-1]
    return not name.startswith(("index", "default"))


def _looks_like_attachment(title: str, url: str) -> bool:
    if _url_has_file_extension(url):
        return True
    normalized = normalize_text(title)
    return any(term in normalized for term in ("职位表", "岗位表", "岗位条件简介表", "简介表", "招聘计划", "招聘岗位", "附件", "报名表"))


def _url_has_file_extension(url: str) -> bool:
    lower_url = url.lower().split("#", 1)[0]
    if lower_url.split("?", 1)[0].endswith((".pdf", ".xlsx", ".xls", ".xlsm", ".doc", ".docx")):
        return True
    # 下载网关链接的扩展名常在查询参数里，例如 download?fileName=岗位简介表.xls
    return "?" in lower_url and bool(re.search(r"\.(pdf|xlsx|xlsm|xls|doc|docx)(?=&|$)", lower_url))
