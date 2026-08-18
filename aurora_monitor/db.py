from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS region (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    level TEXT NOT NULL CHECK (level IN ('province', 'city', 'county')),
    parent_code TEXT REFERENCES region(code),
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS institution (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region_code TEXT NOT NULL REFERENCES region(code),
    ownership TEXT NOT NULL CHECK (ownership IN ('public', 'private', 'unknown')),
    school_level TEXT NOT NULL CHECK (school_level IN ('junior_college', 'vocational', 'undergraduate', 'other')),
    official_domain TEXT NOT NULL UNIQUE,
    official_site_url TEXT NOT NULL,
    whitelist_batch_id TEXT,
    whitelist_row_hash TEXT,
    whitelist_provider TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'pending_review', 'verified', 'suspended', 'retired')),
    verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keyword_policy (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    include_any TEXT NOT NULL DEFAULT '[]',
    exclude_any TEXT NOT NULL DEFAULT '[]',
    workflow_terms TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'retired')),
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS source (
    id TEXT PRIMARY KEY,
    source_group TEXT NOT NULL,
    institution_id TEXT REFERENCES institution(id),
    region_code TEXT NOT NULL REFERENCES region(code),
    publisher TEXT NOT NULL,
    source_level TEXT NOT NULL CHECK (source_level IN ('official', 'school', 'secondary')),
    entry_url TEXT NOT NULL UNIQUE,
    allowed_domains TEXT NOT NULL,
    discovery_type TEXT NOT NULL DEFAULT 'html_list',
    adapter TEXT NOT NULL DEFAULT 'generic_html_v1',
    keyword_policy_id TEXT NOT NULL,
    keyword_policy_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'pending_review', 'verified', 'enabled', 'degraded', 'disabled', 'retired')),
    check_interval_sec INTEGER NOT NULL DEFAULT 21600,
    max_pages INTEGER NOT NULL DEFAULT 1 CHECK (max_pages BETWEEN 1 AND 20),
    next_check_at TEXT,
    last_success_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    etag TEXT,
    last_modified TEXT,
    last_content_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (keyword_policy_id, keyword_policy_version) REFERENCES keyword_policy(id, version)
);

CREATE TABLE IF NOT EXISTS monitor_profile (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    scope_types TEXT NOT NULL DEFAULT '[]',
    region_codes TEXT NOT NULL DEFAULT '[]',
    institution_ids TEXT NOT NULL DEFAULT '[]',
    include_keywords TEXT NOT NULL DEFAULT '[]',
    exclude_keywords TEXT NOT NULL DEFAULT '[]',
    event_types TEXT NOT NULL DEFAULT '["new_notice"]',
    channel TEXT NOT NULL DEFAULT 'console',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_check (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES source(id),
    status TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    response_bytes INTEGER,
    response_ms INTEGER,
    content_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notice (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source(id),
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('candidate', 'needs_review', 'noise')),
    matched_terms TEXT NOT NULL DEFAULT '[]',
    detail_status TEXT NOT NULL DEFAULT 'pending' CHECK (detail_status IN ('pending', 'fetched', 'failed')),
    detail_failures INTEGER NOT NULL DEFAULT 0,
    next_detail_retry_at TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, url)
);

CREATE TABLE IF NOT EXISTS evidence_version (
    id TEXT PRIMARY KEY,
    notice_id TEXT NOT NULL REFERENCES notice(id),
    source_url TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_type TEXT,
    body BLOB,
    object_path TEXT,
    extracted_text TEXT NOT NULL DEFAULT '',
    parser_status TEXT NOT NULL DEFAULT 'not_parsed',
    parser_warnings TEXT NOT NULL DEFAULT '[]',
    retrieved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(notice_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS position (
    id TEXT PRIMARY KEY,
    notice_id TEXT NOT NULL REFERENCES notice(id),
    evidence_id TEXT NOT NULL REFERENCES evidence_version(id),
    sheet_name TEXT NOT NULL DEFAULT '',
    row_index INTEGER NOT NULL,
    position_code TEXT NOT NULL DEFAULT 'unknown',
    employer TEXT NOT NULL DEFAULT 'unknown',
    position_name TEXT NOT NULL DEFAULT 'unknown',
    work_location TEXT NOT NULL DEFAULT 'unknown',
    headcount TEXT NOT NULL DEFAULT 'unknown',
    education TEXT NOT NULL DEFAULT 'unknown',
    degree TEXT NOT NULL DEFAULT 'unknown',
    major_requirement TEXT NOT NULL DEFAULT 'unknown',
    fresh_graduate_requirement TEXT NOT NULL DEFAULT 'unknown',
    grassroots_requirement TEXT NOT NULL DEFAULT 'unknown',
    political_requirement TEXT NOT NULL DEFAULT 'unknown',
    certificate_requirement TEXT NOT NULL DEFAULT 'unknown',
    age_requirement TEXT NOT NULL DEFAULT 'unknown',
    gender_requirement TEXT NOT NULL DEFAULT 'unknown',
    household_requirement TEXT NOT NULL DEFAULT 'unknown',
    application_schedule TEXT NOT NULL DEFAULT 'unknown',
    other_requirements TEXT NOT NULL DEFAULT 'unknown',
    raw_row TEXT NOT NULL DEFAULT '[]',
    header_row TEXT NOT NULL DEFAULT '[]',
    parser_version INTEGER NOT NULL DEFAULT 1,
    parsed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(evidence_id, sheet_name, row_index)
);

CREATE TABLE IF NOT EXISTS notification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL REFERENCES monitor_profile(id),
    notice_id TEXT NOT NULL REFERENCES notice(id),
    event_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    UNIQUE(profile_id, notice_id, event_type)
);

CREATE TABLE IF NOT EXISTS user_profile (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    profile_json TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_profile_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL REFERENCES user_profile(id),
    version INTEGER NOT NULL,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(profile_id, version)
);

CREATE TABLE IF NOT EXISTS recommendation_run (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES user_profile(id),
    profile_version INTEGER NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    llm_used INTEGER NOT NULL DEFAULT 0,
    llm_model TEXT,
    llm_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS source_due_idx ON source(status, next_check_at);
CREATE INDEX IF NOT EXISTS source_region_idx ON source(region_code, source_group, status);
CREATE INDEX IF NOT EXISTS notice_source_idx ON notice(source_id, last_seen_at);
CREATE INDEX IF NOT EXISTS position_notice_idx ON position(notice_id);
CREATE INDEX IF NOT EXISTS profile_user_idx ON monitor_profile(user_id, enabled);
CREATE INDEX IF NOT EXISTS recommendation_profile_idx ON recommendation_run(profile_id, created_at);
"""


def _json(value: Any) -> str:
    return json.dumps(value or [], ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        database_path = Path(path)
        self.object_dir = database_path.with_name(database_path.stem + "_objects")
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.connection.close()

    def store_object(self, digest: str, body: bytes) -> str:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise ValueError("object digest must be a SHA-256 hex string")
        relative_path = Path(digest[:2]) / digest
        target = self.object_dir / relative_path
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(body)
            os.replace(temporary, target)
        return str(relative_path)

    def read_object(self, relative_path: str) -> bytes:
        """Read an evidence object while preventing paths outside object_dir."""
        candidate = (self.object_dir / relative_path).resolve()
        object_root = self.object_dir.resolve()
        if candidate != object_root and object_root not in candidate.parents:
            raise ValueError("object path escapes evidence object directory")
        return candidate.read_bytes()

    def read_evidence(self, evidence_id: str) -> bytes:
        """Return evidence bytes from object storage, with legacy BLOB fallback."""
        row = self.connection.execute(
            "SELECT object_path, body FROM evidence_version WHERE id = ?", (evidence_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"evidence not found: {evidence_id}")
        if row["object_path"]:
            return self.read_object(row["object_path"])
        if row["body"] is not None:
            return bytes(row["body"])
        raise FileNotFoundError(f"evidence object missing: {evidence_id}")

    def init_schema(self) -> None:
        self.connection.executescript(SCHEMA)
        self._ensure_column("evidence_version", "extracted_text", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("evidence_version", "parser_status", "TEXT NOT NULL DEFAULT 'not_parsed'")
        self._ensure_column("evidence_version", "parser_warnings", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("source", "max_pages", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("monitor_profile", "channel", "TEXT NOT NULL DEFAULT 'console'")
        self._ensure_column("notification", "attempts", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("notification", "error_message", "TEXT")
        self._ensure_column("notice", "detail_status", "TEXT NOT NULL DEFAULT 'pending'")
        self._ensure_column("notice", "detail_failures", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("notice", "next_detail_retry_at", "TEXT")
        self._ensure_column("evidence_version", "source_url", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("evidence_version", "object_path", "TEXT")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def seed_regions(self) -> None:
        cities = ["南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁"]
        with self.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO region(code, name, level) VALUES ('JS', '江苏', 'province')")
            for city in cities:
                conn.execute(
                    "INSERT OR IGNORE INTO region(code, name, level, parent_code) VALUES (?, ?, 'city', 'JS')",
                    (f"JS-{city}", city),
                )

    def upsert_policy(self, policy_id: str, version: int, include: list[str], exclude: list[str], workflow: list[str]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO keyword_policy(id, version, include_any, exclude_any, workflow_terms) VALUES (?, ?, ?, ?, ?)",
                (policy_id, version, _json(include), _json(exclude), _json(workflow)),
            )

    def import_institutions_csv(self, path: str | Path, batch_id: str, provider: str = "user") -> dict[str, int]:
        inserted = updated = rejected = 0
        raw = Path(path).read_bytes()
        batch_hash = hashlib.sha256(raw).hexdigest()
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle, self.transaction() as conn:
            for row in csv.DictReader(handle):
                required = ["institution_id", "institution_name", "city", "ownership", "school_level", "official_domain", "official_site_url"]
                if any(not row.get(field, "").strip() for field in required):
                    rejected += 1
                    continue
                region = conn.execute("SELECT code FROM region WHERE name = ? AND level = 'city'", (row["city"].strip(),)).fetchone()
                if not region or row["ownership"].strip() != "public" or row["school_level"].strip() not in {"junior_college", "vocational"}:
                    rejected += 1
                    continue
                row_hash = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                existing = conn.execute("SELECT id FROM institution WHERE id = ?", (row["institution_id"].strip(),)).fetchone()
                conn.execute(
                    """INSERT INTO institution(id, name, region_code, ownership, school_level, official_domain,
                       official_site_url, whitelist_batch_id, whitelist_row_hash, whitelist_provider, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified')
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name, region_code=excluded.region_code,
                       ownership=excluded.ownership, school_level=excluded.school_level, official_domain=excluded.official_domain,
                       official_site_url=excluded.official_site_url, whitelist_batch_id=excluded.whitelist_batch_id,
                       whitelist_row_hash=excluded.whitelist_row_hash, whitelist_provider=excluded.whitelist_provider,
                       status='verified', updated_at=CURRENT_TIMESTAMP""",
                    (row["institution_id"].strip(), row["institution_name"].strip(), region["code"], row["ownership"].strip(), row["school_level"].strip(), row["official_domain"].strip(), row["official_site_url"].strip(), batch_id, f"{batch_hash}:{row_hash}", provider),
                )
                inserted += int(existing is None)
                updated += int(existing is not None)
        return {"inserted": inserted, "updated": updated, "rejected": rejected}

    def validate_institutions_csv(self, path: str | Path) -> dict[str, Any]:
        required = ["institution_id", "institution_name", "city", "ownership", "school_level", "official_domain", "official_site_url"]
        errors: list[dict[str, Any]] = []
        valid = 0
        seen_ids: set[str] = set()
        seen_domains: set[str] = set()
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            for line_number, row in enumerate(csv.DictReader(handle), start=2):
                row_errors: list[str] = []
                missing = [field for field in required if not row.get(field, "").strip()]
                if missing:
                    row_errors.append(f"missing fields: {', '.join(missing)}")
                institution_id = row.get("institution_id", "").strip()
                domain = row.get("official_domain", "").strip().lower()
                if institution_id in seen_ids:
                    row_errors.append("duplicate institution_id in file")
                if domain in seen_domains and domain:
                    row_errors.append("duplicate official_domain in file")
                seen_ids.add(institution_id)
                seen_domains.add(domain)
                if row.get("city", "").strip() not in {"南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁"}:
                    row_errors.append("city is not one of Jiangsu's 13 prefecture-level cities")
                if row.get("ownership", "").strip() != "public":
                    row_errors.append("ownership must be public")
                if row.get("school_level", "").strip() not in {"junior_college", "vocational"}:
                    row_errors.append("school_level must be junior_college or vocational")
                if row_errors:
                    errors.append({"line": line_number, "institution_id": institution_id, "errors": row_errors})
                else:
                    valid += 1
        return {"valid": valid, "invalid": len(errors), "errors": errors}

    def add_source(self, source: dict[str, Any]) -> None:
        entry_url = source["entry_url"]
        parsed = urlparse(entry_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or any(char in parsed.hostname for char in "<> \t\r\n"):
            raise ValueError(f"invalid source URL: {entry_url}")
        existing_url = self.connection.execute(
            "SELECT id FROM source WHERE entry_url = ? AND id <> ?", (entry_url, source["id"])
        ).fetchone()
        if existing_url:
            raise ValueError(
                f"source entry_url already belongs to {existing_url['id']}: {entry_url}; "
                "use one source record or provide a different official column URL"
            )
        region = self.connection.execute("SELECT code FROM region WHERE code = ?", (source["region_code"],)).fetchone()
        if not region:
            raise ValueError(f"unknown source region: {source['region_code']}")
        institution_id = source.get("institution_id")
        if source["source_group"] == "jiangsu_public_college" and not institution_id:
            raise ValueError("public college source requires institution_id")
        if institution_id:
            institution = self.connection.execute("SELECT region_code, status FROM institution WHERE id = ?", (institution_id,)).fetchone()
            if not institution:
                raise ValueError(f"unknown institution: {institution_id}")
            if source["source_group"] == "jiangsu_public_college" and (institution["status"] != "verified" or institution["region_code"] != source["region_code"]):
                raise ValueError("public college source requires a verified institution in the same region")
        allowed_domains = source.get("allowed_domains") or [parsed.hostname]
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO source(id, source_group, institution_id, region_code, publisher, source_level,
                   entry_url, allowed_domains, discovery_type, adapter, keyword_policy_id,
                   keyword_policy_version, status, max_pages, next_check_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(id) DO UPDATE SET
                     source_group=excluded.source_group,
                     institution_id=excluded.institution_id,
                     region_code=excluded.region_code,
                     publisher=excluded.publisher,
                     source_level=excluded.source_level,
                     entry_url=excluded.entry_url,
                     allowed_domains=excluded.allowed_domains,
                     discovery_type=excluded.discovery_type,
                     adapter=excluded.adapter,
                     keyword_policy_id=excluded.keyword_policy_id,
                     keyword_policy_version=excluded.keyword_policy_version,
                     status=excluded.status,
                     max_pages=excluded.max_pages,
                     updated_at=CURRENT_TIMESTAMP""",
                (source["id"], source["source_group"], institution_id, source["region_code"], source["publisher"], source.get("source_level", "official"), entry_url, _json(allowed_domains), source.get("discovery_type", "html_list"), source.get("adapter", "generic_html_v1"), source["keyword_policy_id"], source.get("keyword_policy_version", 1), source.get("status", "enabled"), max(1, min(int(source.get("max_pages", 1)), 20))),
            )

    def add_profile(self, profile: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO monitor_profile(id, user_id, name, enabled, scope_types, region_codes,
                   institution_ids, include_keywords, exclude_keywords, event_types, channel)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (profile["id"], profile["user_id"], profile["name"], int(profile.get("enabled", True)), _json(profile.get("scope_types")), _json(profile.get("region_codes")), _json(profile.get("institution_ids")), _json(profile.get("include_keywords")), _json(profile.get("exclude_keywords")), _json(profile.get("event_types", ["new_notice"])), profile.get("channel", "console")),
            )

    def import_sources_json(self, path: str | Path) -> int:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        sources = payload.get("sources", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        self._validate_source_batch(sources)
        for source in sources:
            self.add_source(source)
        return len(sources)

    def import_sources_yaml(self, path: str | Path) -> int:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError("YAML import requires PyYAML") from exc
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        sources = payload.get("sources", [])
        normalized_sources = []
        for item in sources:
            source = dict(item)
            source_id = source.get("id", "<missing id>")
            if "region" not in source:
                raise ValueError(f"source {source_id} is missing region")
            region = self.connection.execute("SELECT code FROM region WHERE name = ?", (source.pop("region"),)).fetchone()
            if not region:
                raise ValueError(f"source {source_id} has unknown source region")
            source["region_code"] = region["code"]
            source["keyword_policy_id"] = source.get("keyword_policy_id") or payload.get("policy", {}).get("keyword_policy")
            source["status"] = "enabled" if source.pop("enabled", True) else "disabled"
            source["allowed_domains"] = source.get("allowed_domains") or [urlparse(source.get("entry_url", "")).hostname]
            normalized_sources.append(source)
        self._validate_source_batch(normalized_sources)
        imported = 0
        for source in normalized_sources:
            self.add_source(source)
            imported += 1
        return imported

    def _validate_source_batch(self, sources: list[dict[str, Any]]) -> None:
        seen_ids: dict[str, int] = {}
        seen_urls: dict[str, str] = {}
        for index, source in enumerate(sources, start=1):
            source_id = str(source.get("id", "")).strip()
            entry_url = str(source.get("entry_url", "")).strip()
            if not source_id:
                raise ValueError(f"source row {index} is missing id")
            if source_id in seen_ids:
                raise ValueError(f"duplicate source id in import: {source_id}")
            seen_ids[source_id] = index
            if not entry_url:
                raise ValueError(f"source {source_id} is missing entry_url")
            if "<待核验>" in entry_url or "<学校官方域名待核验>" in entry_url or "<" in entry_url or ">" in entry_url:
                raise ValueError(f"source {source_id} still contains a placeholder URL: {entry_url}")
            if entry_url in seen_urls:
                raise ValueError(
                    f"duplicate entry_url in import: {entry_url} "
                    f"(sources {seen_urls[entry_url]} and {source_id}); "
                    "keep one source or replace one URL with its actual official column"
                )
            seen_urls[entry_url] = source_id

    def due_sources(self) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM source WHERE status IN ('enabled', 'degraded') AND (next_check_at IS NULL OR next_check_at <= CURRENT_TIMESTAMP) ORDER BY next_check_at").fetchall()

    def list_profile_notices(self, profile_id: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT n.id, n.title, n.url, n.published_at, n.decision, n.matched_terms,
                      s.source_group, s.publisher, s.region_code, i.name AS institution_name,
                      x.status AS notification_status, x.created_at AS notified_at
               FROM notification x
               JOIN notice n ON n.id = x.notice_id
               JOIN source s ON s.id = n.source_id
               LEFT JOIN institution i ON i.id = s.institution_id
               WHERE x.profile_id = ?
               ORDER BY x.created_at DESC LIMIT ?""",
            (profile_id, max(1, min(limit, 500))),
        ).fetchall()

    def list_notices(self, decision: str = "candidate", limit: int = 50) -> list[sqlite3.Row]:
        if decision not in {"candidate", "needs_review", "noise", "all"}:
            raise ValueError("decision must be candidate, needs_review, noise, or all")
        where = "" if decision == "all" else "WHERE n.decision = ?"
        params: tuple[Any, ...] = () if decision == "all" else (decision,)
        params += (max(1, min(limit, 500)),)
        return self.connection.execute(
            f"""SELECT n.id, n.title, n.url, n.published_at, n.decision,
                       n.matched_terms, n.detail_status, n.first_seen_at,
                       n.last_seen_at, s.id AS source_id, s.publisher,
                       s.source_group, s.region_code,
                       i.name AS institution_name
                FROM notice n
                JOIN source s ON s.id = n.source_id
                LEFT JOIN institution i ON i.id = s.institution_id
                {where}
                ORDER BY n.first_seen_at DESC
                LIMIT ?""",
            params,
        ).fetchall()

    def list_source_health(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT s.id, s.publisher, s.region_code, s.source_group, s.status,
                      s.last_success_at, s.consecutive_failures, s.next_check_at,
                      c.status AS last_check_status, c.checked_at AS last_checked_at,
                      c.error_message AS last_error
               FROM source s
               LEFT JOIN source_check c ON c.id = (
                   SELECT MAX(id) FROM source_check WHERE source_id = s.id
               )
               ORDER BY s.region_code, s.id LIMIT ?""",
            (max(1, min(limit, 500)),),
        ).fetchall()

    def pending_notifications(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT x.id, x.profile_id, x.notice_id, x.event_type, x.attempts,
                      p.user_id, p.channel, p.name AS profile_name,
                      n.title, n.url, n.published_at,
                      s.publisher, s.region_code, i.name AS institution_name
               FROM notification x
               JOIN monitor_profile p ON p.id = x.profile_id
               JOIN notice n ON n.id = x.notice_id
               JOIN source s ON s.id = n.source_id
               LEFT JOIN institution i ON i.id = s.institution_id
               WHERE x.status IN ('pending', 'failed') AND x.attempts < 3
               ORDER BY x.created_at LIMIT ?""",
            (max(1, min(limit, 500)),),
        ).fetchall()

    def mark_notification_sent(self, notification_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE notification SET status='sent', attempts=attempts+1, error_message=NULL, sent_at=CURRENT_TIMESTAMP WHERE id=?",
                (notification_id,),
            )

    def mark_notification_failed(self, notification_id: int, message: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE notification SET status='failed', attempts=attempts+1, error_message=? WHERE id=?",
                (message[:1000], notification_id),
            )

    def active_policies(self, policy_id: str, version: int) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM keyword_policy WHERE id = ? AND version = ?", (policy_id, version)).fetchone()
        if not row:
            raise ValueError(f"keyword policy not found: {policy_id}/{version}")
        return row

    def add_policy_json(self, path: str | Path) -> int:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        policies = payload.get("policies", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        for policy in policies:
            self.upsert_policy(policy["id"], int(policy.get("version", 1)), policy.get("include_any", []), policy.get("exclude_any", []), policy.get("workflow_terms", []))
        return len(policies)
