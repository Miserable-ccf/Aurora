from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from aurora_monitor.db import Database

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

    app.state.database = database
    app.state.repository = repository
    app.state.recommendation_service = service
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

    @app.post("/api/v1/recommendations", response_model=RecommendationResponse)
    def recommendations(request: RecommendationRequest) -> RecommendationResponse:
        try:
            return service.recommend(request.profile, request.save_profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
