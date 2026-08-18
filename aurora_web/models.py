from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ExamType = Literal["civil_service", "public_institution", "public_college"]


class UserProfile(BaseModel):
    user_id: str = "local-user"
    exam_types: list[ExamType] = Field(default_factory=lambda: ["public_institution"])
    year: int = Field(default_factory=lambda: datetime.now().year, ge=2020, le=2100)
    region_codes: list[str] = Field(default_factory=lambda: ["JS"])
    education: str = ""
    degree: str = ""
    major: str = ""
    graduate_status: str = "unknown"
    political_status: str = ""
    grassroots_years: int | None = Field(default=None, ge=0, le=50)
    certificates: list[str] = Field(default_factory=list)
    preferred_roles: list[str] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    include_process_updates: bool = False
    max_results: int = Field(default=20, ge=1, le=50)

    @field_validator(
        "exam_types",
        "region_codes",
        "certificates",
        "preferred_roles",
        "include_keywords",
        "exclude_keywords",
    )
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            item = str(value).strip()
            if item and item not in result:
                result.append(item)
        return result

    @field_validator("user_id", "education", "degree", "major", "graduate_status", "political_status")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return str(value or "").strip()


class RecommendationRequest(BaseModel):
    profile: UserProfile
    save_profile: bool = True


class RecommendationItem(BaseModel):
    notice_id: str
    title: str
    url: str
    publisher: str
    region_code: str
    source_group: str
    institution_name: str | None = None
    published_at: str | None = None
    first_seen_at: str
    detail_status: str
    score: int
    match_level: Literal["relevant", "needs_review"]
    reasons: list[str]
    matched_terms: list[str]
    evidence_excerpt: str
    summary: str
    checks: list[str]


class RecommendationResponse(BaseModel):
    run_id: str
    profile_version: int
    overview: str
    items: list[RecommendationItem]
    warnings: list[str]
    llm_used: bool
    llm_model: str
    llm_error: str
    generated_at: str
