from __future__ import annotations

import json
import uuid
from typing import Any

from aurora_monitor.db import Database

from .models import UserProfile


class WebRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    def get_profile(self, user_id: str = "local-user") -> tuple[UserProfile, int]:
        row = self.db.connection.execute(
            "SELECT profile_json, current_version FROM user_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return UserProfile(user_id=user_id), 0
        return UserProfile.model_validate_json(row["profile_json"]), int(row["current_version"])

    def save_profile(self, profile: UserProfile) -> int:
        profile_id = f"profile:{profile.user_id}"
        payload = profile.model_dump_json()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT current_version, profile_json FROM user_profile WHERE id = ?", (profile_id,)
            ).fetchone()
            if row and row["profile_json"] == payload:
                return int(row["current_version"])
            version = int(row["current_version"] if row else 0) + 1
            conn.execute(
                """INSERT INTO user_profile(id, user_id, profile_json, current_version)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json,
                   current_version=excluded.current_version, updated_at=CURRENT_TIMESTAMP""",
                (profile_id, profile.user_id, payload, version),
            )
            conn.execute(
                "INSERT INTO user_profile_version(profile_id, version, profile_json) VALUES (?, ?, ?)",
                (profile_id, version, payload),
            )
        return version

    def search_candidate_notices(self, limit: int = 300) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """SELECT n.id, n.title, n.url, n.published_at, n.matched_terms,
                      n.detail_status, n.first_seen_at, n.last_seen_at,
                      s.id AS source_id, s.publisher, s.source_group,
                      s.region_code, s.source_level, i.name AS institution_name,
                      e.id AS evidence_id, e.extracted_text, e.retrieved_at
               FROM notice n
               JOIN source s ON s.id = n.source_id
               LEFT JOIN institution i ON i.id = s.institution_id
               LEFT JOIN evidence_version e ON e.id = (
                   SELECT ev.id FROM evidence_version ev
                   WHERE ev.notice_id = n.id
                   ORDER BY ev.retrieved_at DESC, ev.rowid DESC LIMIT 1
               )
               WHERE n.decision = 'candidate' AND n.detail_status = 'fetched'
               ORDER BY COALESCE(n.published_at, n.first_seen_at) DESC
               LIMIT ?""",
            (max(1, min(limit, 1000)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def positions_for_notice(self, notice_id: str) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            "SELECT * FROM position WHERE notice_id = ? ORDER BY sheet_name, row_index",
            (notice_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def position_by_id(self, position_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM position WHERE id = ?", (position_id,)
        ).fetchone()
        return dict(row) if row else None

    def notice_brief(self, notice_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            """SELECT n.id, n.title, n.url, s.publisher
               FROM notice n LEFT JOIN source s ON s.id = n.source_id
               WHERE n.id = ?""",
            (notice_id,),
        ).fetchone()
        return dict(row) if row else None

    def evidence_for_notice(self, notice_id: str) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """SELECT id, source_url, content_type, parser_status, content_sha256,
                      retrieved_at, object_path
               FROM evidence_version WHERE notice_id = ? ORDER BY retrieved_at""",
            (notice_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_recommendation_run(
        self,
        profile: UserProfile,
        profile_version: int,
        result: dict[str, Any],
        llm_used: bool,
        llm_model: str,
        llm_error: str,
    ) -> str:
        run_id = uuid.uuid4().hex
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO recommendation_run(
                   id, profile_id, profile_version, request_json, result_json,
                   llm_used, llm_model, llm_error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    f"profile:{profile.user_id}",
                    profile_version,
                    profile.model_dump_json(),
                    json.dumps(result, ensure_ascii=False),
                    int(llm_used),
                    llm_model,
                    llm_error[:1000],
                ),
            )
        return run_id
