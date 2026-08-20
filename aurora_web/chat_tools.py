"""对话式推荐的工具层：把现有 aurora_monitor/aurora_web 能力包装成 LLM 可调用的函数。

设计原则：
- 所有工具都只是现有确定性代码的薄包装，LLM 不能绕过资格核验与检索逻辑；
- search_positions 的 profile 由服务端注入（已确认画像），模型不可编造；
- 返回结构全部为 JSON 可序列化 dict。
"""
from __future__ import annotations

import json
from typing import Any

from aurora_monitor.eligibility import evaluate_position_row
from aurora_monitor.major_catalog import load_major_catalog

from .models import UserProfile
from .repository import WebRepository


EXAM_TYPE_LABELS = {
    "civil_service": "公务员",
    "public_institution": "事业单位",
    "public_college": "公办大专",
}

REGIONS = ["南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁"]


class ChatToolbox:
    def __init__(self, repository: WebRepository) -> None:
        self.repository = repository
        self.major_catalog = load_major_catalog()

    # ---------- 工具实现 ----------

    def list_options(self, kind: str) -> dict[str, Any]:
        if kind == "exam_types":
            return {"options": [{"value": key, "label": label} for key, label in EXAM_TYPE_LABELS.items()]}
        if kind == "regions":
            return {"options": [{"value": "JS", "label": "江苏全省"}] + [{"value": f"JS-{name}", "label": name} for name in REGIONS]}
        if kind == "major_categories":
            if not self.major_catalog:
                return {"options": []}
            return {"options": sorted(self.major_catalog.category_names())}
        return {"error": f"unknown options kind: {kind}"}

    def submit_profile(self, profile_json: dict[str, Any]) -> dict[str, Any]:
        try:
            profile = UserProfile.model_validate(profile_json)
        except Exception as exc:
            return {"ok": False, "error": f"画像校验失败：{exc}"}
        missing = _missing_required(profile)
        return {"ok": not missing, "missing_fields": missing, "profile": _profile_view(profile)}

    def search_positions(self, profile: UserProfile, limit: int = 30) -> dict[str, Any]:
        from .recommendation import RecommendationService  # 延迟导入避免循环

        service = RecommendationService(self.repository)
        rows = service.recommend_positions(profile, limit=max(1, min(int(limit or 30), 60)))
        return {"count": len(rows), "positions": rows}

    def check_eligibility(self, position_id: str, profile: UserProfile) -> dict[str, Any]:
        row = self.repository.position_by_id(position_id)
        if not row:
            return {"error": "position not found"}
        evaluation = evaluate_position_row(row, profile)
        return {
            "position_id": position_id,
            "position_name": row.get("position_name") or "",
            "verdict": evaluation.verdict,
            "conditions": [
                {"field": check.field, "label": check.label, "requirement": check.requirement, "verdict": check.verdict, "reason": check.reason}
                for check in evaluation.checks
            ],
            "questions": evaluation.questions,
        }

    def get_position_detail(self, position_id: str) -> dict[str, Any]:
        row = self.repository.position_by_id(position_id)
        if not row:
            return {"error": "position not found"}
        notice = self.repository.notice_brief(row["notice_id"]) or {}
        fields = {
            key: row.get(key)
            for key in (
                "id", "position_code", "employer", "position_name", "work_location", "headcount",
                "education", "degree", "major_requirement", "fresh_graduate_requirement",
                "political_requirement", "certificate_requirement", "age_requirement",
                "gender_requirement", "household_requirement", "other_requirements",
            )
        }
        return {
            "position": fields,
            "notice_id": row["notice_id"],
            "notice_title": notice.get("title", ""),
            "notice_url": notice.get("url", ""),
        }

    # ---------- 注册表 ----------

    def execute(self, name: str, arguments: dict[str, Any], confirmed_profile: UserProfile | None) -> dict[str, Any]:
        if name == "list_options":
            return self.list_options(str(arguments.get("kind", "")))
        if name == "submit_profile":
            return self.submit_profile(arguments.get("profile") or {})
        if name == "search_positions":
            if confirmed_profile is None:
                return {"error": "画像尚未确认，不能检索岗位"}
            return self.search_positions(confirmed_profile, int(arguments.get("limit", 30)))
        if name == "check_eligibility":
            if confirmed_profile is None:
                return {"error": "画像尚未确认，不能核验资格"}
            return self.check_eligibility(str(arguments.get("position_id", "")), confirmed_profile)
        if name == "get_position_detail":
            return self.get_position_detail(str(arguments.get("position_id", "")))
        return {"error": f"unknown tool: {name}"}


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_options",
                "description": "查询可选项字典：exam_types 招考类型 / regions 地区 / major_categories 专业大类",
                "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["exam_types", "regions", "major_categories"]}}, "required": ["kind"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_profile",
                "description": "提交/校验用户画像，返回缺失的必填字段",
                "parameters": {"type": "object", "properties": {"profile": {"type": "object"}}, "required": ["profile"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_positions",
                "description": "按已确认画像检索岗位（含资格核验结果与匹配分），服务端自动注入画像",
                "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_eligibility",
                "description": "查询单个岗位的逐条资格核验明细",
                "parameters": {"type": "object", "properties": {"position_id": {"type": "string"}}, "required": ["position_id"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_position_detail",
                "description": "查询岗位完整信息与公告来源链接",
                "parameters": {"type": "object", "properties": {"position_id": {"type": "string"}}, "required": ["position_id"]},
            },
        },
    ]


def _missing_required(profile: UserProfile) -> list[str]:
    missing = []
    if not profile.exam_types:
        missing.append("exam_types")
    if not profile.education:
        missing.append("education")
    if not profile.degree:
        missing.append("degree")
    if not profile.major:
        missing.append("major")
    if not profile.region_codes:
        missing.append("region_codes")
    return missing


def _profile_view(profile: UserProfile) -> dict[str, Any]:
    view = profile.model_dump()
    view.pop("user_id", None)
    return view


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
