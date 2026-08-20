from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from aurora_monitor.db import Database
from aurora_monitor.eligibility import evaluate_position_row

from .chat import ChatOrchestrator
from .config import load_local_env
from .models import RecommendationRequest, RecommendationResponse, UserProfile
from .recommendation import RecommendationService
from .repository import WebRepository


STATIC_DIR = Path(__file__).with_name("static")
REGIONS = ["南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁"]
load_local_env()


def create_app(database_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Aurora 招考信息工作台", version="0.2.0")
    db_path = Path(database_path or os.getenv("AURORA_DB_PATH", "aurora.db"))
    database = Database(db_path)
    database.init_schema()
    database.seed_regions()
    repository = WebRepository(database)
    service = RecommendationService(repository)
    chat_orchestrator = ChatOrchestrator(repository)

    app.state.database = database
    app.state.repository = repository
    app.state.recommendation_service = service
    app.state.chat_orchestrator = chat_orchestrator
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.on_event("shutdown")
    def close_database() -> None:
        database.close()

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        response = FileResponse(STATIC_DIR / "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    def health() -> dict:
        source_count = database.connection.execute("SELECT COUNT(*) FROM source WHERE status='enabled'").fetchone()[0]
        notice_count = database.connection.execute("SELECT COUNT(*) FROM notice WHERE decision='candidate'").fetchone()[0]
        return {
            "status": "ok",
            "database": db_path.name,
            "enabled_sources": source_count,
            "candidate_notices": notice_count,
            "llm_configured": service.llm.enabled,
        }

    @app.get("/api/v1/options")
    def options() -> dict:
        return {
            "exam_types": [
                {"value": "civil_service", "label": "公务员"},
                {"value": "public_institution", "label": "事业单位"},
                {"value": "public_college", "label": "公办大专"},
            ],
            "regions": [{"value": "JS", "label": "江苏全省"}]
            + [{"value": f"JS-{name}", "label": name} for name in REGIONS],
        }

    @app.get("/api/v1/profile", response_model=UserProfile)
    def get_profile(user_id: str = "local-user") -> UserProfile:
        profile, _ = repository.get_profile(user_id)
        return profile

    @app.put("/api/v1/profile")
    def put_profile(profile: UserProfile) -> dict:
        version = repository.save_profile(profile)
        return {"saved": True, "version": version, "profile": profile}

    @app.post("/api/v1/chat")
    def chat(request: dict) -> dict:
        message = str(request.get("message") or "").strip()
        session_id = request.get("session_id") or None
        profile_patch = request.get("profile_patch") if isinstance(request.get("profile_patch"), dict) else None
        if not message and not profile_patch:
            raise HTTPException(status_code=400, detail="message or profile_patch is required")
        return chat_orchestrator.handle(message, session_id=session_id, profile_patch=profile_patch)

    @app.post("/api/v1/recommendations", response_model=RecommendationResponse)
    def recommendations(request: RecommendationRequest) -> RecommendationResponse:
        try:
            return service.recommend(request.profile, request.save_profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/positions/{position_id}")
    def position_detail(position_id: str, user_id: str = "local-user") -> dict:
        row = repository.position_by_id(position_id)
        if not row:
            raise HTTPException(status_code=404, detail="position not found")
        profile, _ = repository.get_profile(user_id)
        evaluation = evaluate_position_row(row, profile)
        position = {key: value for key, value in row.items() if key not in {"raw_row", "header_row"}}
        for key in ("raw_row", "header_row"):
            try:
                position[key] = json.loads(row[key] or "[]")
            except json.JSONDecodeError:
                position[key] = []
        notice = repository.notice_brief(row["notice_id"]) or {}
        return {
            "position": position,
            "notice_id": row["notice_id"],
            "evidence_id": row["evidence_id"],
            "notice_title": notice.get("title", ""),
            "notice_url": notice.get("url", ""),
            "verdict": evaluation.verdict,
            "conditions": [
                {
                    "field": check.field,
                    "label": check.label,
                    "requirement": "未列出" if check.requirement in {"", "unknown"} else check.requirement,
                    "verdict": check.verdict,
                    "reason": check.reason,
                }
                for check in evaluation.checks
            ],
            "questions": evaluation.questions,
            "sources": [
                {
                    "evidence_id": evidence["id"],
                    "source_url": evidence["source_url"],
                    "content_type": evidence["content_type"] or "",
                    "parser_status": evidence["parser_status"],
                    "content_sha256": evidence["content_sha256"],
                    "retrieved_at": evidence["retrieved_at"],
                    "has_file": bool(evidence["object_path"]),
                    "is_origin": evidence["id"] == row["evidence_id"],
                }
                for evidence in repository.evidence_for_notice(row["notice_id"])
            ],
        }

    @app.get("/api/v1/evidence/{evidence_id}/file")
    def evidence_file(evidence_id: str) -> Response:
        evidence = database.connection.execute(
            "SELECT object_path, content_type, source_url FROM evidence_version WHERE id = ?",
            (evidence_id,),
        ).fetchone()
        if not evidence or not evidence["object_path"]:
            raise HTTPException(status_code=404, detail="evidence file not found")
        body = database.read_object(evidence["object_path"])
        parsed_url = urlparse(evidence["source_url"])
        query_filename = parse_qs(parsed_url.query).get("fileName", [""])[0]
        filename = query_filename or unquote(parsed_url.path.split("/")[-1]) or evidence_id
        return Response(
            content=body,
            media_type=evidence["content_type"] or "application/octet-stream",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"},
        )

    return app


app = create_app()
