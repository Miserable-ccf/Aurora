from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .llm import LLMClient
from .models import (
    ConditionItem,
    ExcludedNotice,
    ExcludedPosition,
    PositionEvaluation,
    RecommendationItem,
    RecommendationResponse,
    UserProfile,
)
from .repository import WebRepository

from aurora_monitor.eligibility import VERDICT_FAIL, evaluate_position_row
from aurora_monitor.positions import UNKNOWN


JOBFAIR_TERMS = (
    "春风行动",
    "就业援助",
    "招聘活动",
    "招聘会",
    "人才交流会",
)

PROCESS_TERMS = (
    "成绩公告",
    "成绩查询",
    "成绩公示",
    "拟聘用",
    "拟录用",
    "面试人选",
    "资格复审",
    "体检名单",
    "考察名单",
    "递补",
    "通知单打印",
)

TYPE_TERMS = {
    "civil_service": ("公务员", "选调", "遴选", "考试录用"),
    "public_institution": ("事业单位", "公开招聘", "招聘工作人员", "编制"),
    "public_college": ("学院", "高等专科学校", "职业技术学院", "教师", "辅导员"),
}

TYPE_LABELS = {
    "civil_service": "公务员",
    "public_institution": "事业单位",
    "public_college": "公办大专",
}


class RecommendationService:
    def __init__(self, repository: WebRepository, llm_client: LLMClient | None = None) -> None:
        self.repository = repository
        self.llm = llm_client or LLMClient()

    def recommend(self, profile: UserProfile, save_profile: bool = True) -> RecommendationResponse:
        existing, existing_version = self.repository.get_profile(profile.user_id)
        if save_profile or existing_version == 0:
            profile_version = self.repository.save_profile(profile)
        else:
            profile, profile_version = existing, existing_version

        candidates = []
        excluded_positions: list[dict[str, Any]] = []
        for row in self.repository.search_candidate_notices():
            item = self._build_item(row, profile)
            if not item:
                continue
            item, excluded = self._apply_position_checks(item, profile)
            if excluded:
                excluded_positions.append(excluded)
                continue
            candidates.append(item)
        candidates.sort(key=lambda value: (value.score, value.first_seen_at), reverse=True)
        items = candidates[: profile.max_results]

        llm_result = self.llm.organize(profile, items)
        overview, warnings = self._merge_llm(items, llm_result.data, profile)
        result_payload = {
            "overview": overview,
            "items": [item.model_dump() for item in items],
            "warnings": warnings,
            "excluded_positions": excluded_positions,
        }
        run_id = self.repository.save_recommendation_run(
            profile,
            profile_version,
            result_payload,
            llm_result.used,
            llm_result.model,
            llm_result.error,
        )
        return RecommendationResponse(
            run_id=run_id,
            profile_version=profile_version,
            overview=overview,
            items=items,
            warnings=warnings,
            llm_used=llm_result.used,
            llm_model=llm_result.model,
            llm_error=llm_result.error,
            generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            excluded_notices=[
                ExcludedNotice(
                    notice_id=str(entry.get("notice_id") or ""),
                    title=str(entry.get("title") or ""),
                    url=str(entry.get("url") or ""),
                    positions=[
                        ExcludedPosition(
                            position_code=str(position.get("position_code") or ""),
                            employer=str(position.get("employer") or ""),
                            position_name=str(position.get("position_name") or ""),
                            reasons=[str(reason) for reason in position.get("reasons", [])],
                        )
                        for position in entry.get("positions", [])
                    ],
                )
                for entry in excluded_positions
            ],
        )

    def recommend_positions(self, profile: UserProfile, limit: int = 30) -> list[dict[str, Any]]:
        """按画像返回岗位级候选（对话式推荐使用）：只保留解析出岗位表的公告，逐岗位核验。"""
        results: list[dict[str, Any]] = []
        exclude_terms = [str(term).strip() for term in profile.exclude_keywords if str(term).strip()]
        for row in self.repository.search_candidate_notices():
            # 岗位级排除：公告标题命中排除词则整篇跳过；否则只排除岗位名命中的岗位，
            # 保留同一公告里的其他岗位（比公告级排除更精细）。
            item = self._build_item(row, profile, apply_text_exclude=False)
            if not item:
                continue
            if exclude_terms and any(term in item.title for term in exclude_terms):
                continue
            position_rows = self.repository.positions_for_notice(item.notice_id)
            if not position_rows:
                continue
            for position_row in position_rows:
                position_name = _show(position_row.get("position_name"))
                if exclude_terms and any(term in position_name for term in exclude_terms):
                    continue
                evaluation = evaluate_position_row(position_row, profile)
                score = item.score + (25 if evaluation.verdict == "eligible" else 10 if evaluation.verdict == "needs_review" else 0)
                results.append(
                    {
                        "position_id": position_row.get("id") or "",
                        "position_code": _show(position_row.get("position_code")),
                        "employer": _show(position_row.get("employer")) or item.institution_name or item.publisher,
                        "position_name": _show(position_row.get("position_name")),
                        "work_location": _show(position_row.get("work_location")),
                        "headcount": _show(position_row.get("headcount")),
                        "education": _show(position_row.get("education")),
                        "degree": _show(position_row.get("degree")),
                        "major_requirement": _show(position_row.get("major_requirement")),
                        "verdict": evaluation.verdict,
                        "score": min(score, 125),
                        "notice_id": item.notice_id,
                        "notice_title": item.title,
                        "notice_url": item.url,
                        "published_at": item.published_at,
                        "match_reasons": item.reasons[:4],
                        "questions": evaluation.questions[:3],
                    }
                )
        order = {"eligible": 0, "needs_review": 1, "not_eligible": 2}
        results.sort(key=lambda value: (order.get(value["verdict"], 3), -value["score"]))
        return results[: max(1, min(int(limit or 30), 60))]

    def _build_item(self, row: dict[str, Any], profile: UserProfile, apply_text_exclude: bool = True) -> RecommendationItem | None:
        title = str(row.get("title") or "").strip()
        text = _compact_text(row.get("extracted_text") or "")
        searchable = f"{title}\n{text[:12000]}"
        inferred_types = _infer_exam_types(title, row.get("source_group") or "")
        requested_types = set(profile.exam_types)
        if requested_types and not requested_types.intersection(inferred_types):
            return None
        source_region = str(row.get("region_code") or "")
        if profile.region_codes and not any(
            region == "JS" or source_region == region or source_region.startswith(region + "-")
            for region in profile.region_codes
        ):
            return None
        explicit_years = {int(value) for value in re.findall(r"20\d{2}", title)}
        if explicit_years and profile.year not in explicit_years:
            return None
        if not profile.include_process_updates and any(term in title for term in PROCESS_TERMS):
            return None
        # 招聘会/春风行动等企业岗位集市公告，不属于公务员/事业编/大专招聘
        if any(term in title for term in JOBFAIR_TERMS):
            return None
        if apply_text_exclude and any(term in searchable for term in profile.exclude_keywords):
            return None
        if profile.include_keywords and not any(term in searchable for term in profile.include_keywords):
            return None

        score = 50
        reasons: list[str] = []
        type_names = [TYPE_LABELS[value] for value in profile.exam_types if value in inferred_types]
        if type_names:
            score += 18
            reasons.append("属于你关注的" + "、".join(type_names) + "信息")
        if source_region in profile.region_codes or "JS" in profile.region_codes:
            score += 10
            reasons.append("地区范围匹配")
        if profile.major and profile.major in searchable:
            score += 12
            reasons.append(f"原文提到专业“{profile.major}”")
        role_hits = [term for term in profile.preferred_roles if term in searchable]
        if role_hits:
            score += min(15, 5 * len(role_hits))
            reasons.append("包含偏好方向：" + "、".join(role_hits[:3]))
        keyword_hits = [term for term in profile.include_keywords if term in searchable]
        if keyword_hits:
            score += min(12, 4 * len(keyword_hits))
            reasons.append("命中关注词：" + "、".join(keyword_hits[:3]))
        if profile.year in explicit_years:
            score += 8
            reasons.append(f"公告年份为 {profile.year}")

        matched_terms = _load_string_list(row.get("matched_terms"))
        excerpt = _select_excerpt(text, [profile.major, *profile.preferred_roles, *profile.include_keywords])
        checks = _default_checks(profile, bool(excerpt))
        return RecommendationItem(
            notice_id=row["id"],
            title=title,
            url=row["url"],
            publisher=row["publisher"],
            region_code=source_region,
            source_group=row["source_group"],
            institution_name=row.get("institution_name"),
            published_at=row.get("published_at"),
            first_seen_at=row["first_seen_at"],
            detail_status=row["detail_status"],
            score=min(score, 100),
            match_level="relevant" if score >= 70 else "needs_review",
            reasons=reasons or ["标题符合招考公告规则"],
            matched_terms=matched_terms,
            evidence_excerpt=excerpt,
            summary=_fallback_summary(title, excerpt),
            checks=checks,
        )

    def _apply_position_checks(
        self, item: RecommendationItem, profile: UserProfile
    ) -> tuple[RecommendationItem, dict[str, Any] | None]:
        position_rows = self.repository.positions_for_notice(item.notice_id)
        if not position_rows:
            return item, None
        evaluations = [(row, evaluate_position_row(row, profile)) for row in position_rows]
        eligible = [(row, evaluation) for row, evaluation in evaluations if evaluation.verdict == "eligible"]
        review = [(row, evaluation) for row, evaluation in evaluations if evaluation.verdict == "needs_review"]
        failed = [(row, evaluation) for row, evaluation in evaluations if evaluation.verdict == "not_eligible"]
        if not eligible and not review:
            return item, {
                "notice_id": item.notice_id,
                "title": item.title,
                "url": item.url,
                "positions": [
                    {
                        "position_code": _show(row.get("position_code")),
                        "position_name": _show(row.get("position_name")),
                        "employer": _show(row.get("employer")),
                        "reasons": [check.reason for check in evaluation.checks if check.verdict == VERDICT_FAIL][:4],
                    }
                    for row, evaluation in failed[:20]
                ],
            }
        item.positions = [_position_model(row, evaluation) for row, evaluation in evaluations]
        if eligible:
            item.match_level = "eligible"
            item.score = min(100, item.score + 25)
        else:
            item.match_level = "needs_review"
            item.score = min(100, item.score + 10)
        item.reasons.insert(
            0, f"岗位级核验：{len(eligible)} 个岗位初步符合，{len(review)} 个待核实，{len(failed)} 个不符合"
        )
        questions: list[str] = []
        for _, evaluation in evaluations:
            for question in evaluation.questions:
                if question not in questions:
                    questions.append(question)
        if questions:
            item.checks = questions[:6]
        return item, None

    @staticmethod
    def _merge_llm(
        items: list[RecommendationItem], data: dict, profile: UserProfile
    ) -> tuple[str, list[str]]:
        by_id = {item.notice_id: item for item in items}
        if isinstance(data.get("items"), list):
            for annotation in data["items"]:
                if not isinstance(annotation, dict):
                    continue
                item = by_id.get(str(annotation.get("notice_id") or ""))
                if not item:
                    continue
                summary = str(annotation.get("summary") or "").strip()
                checks = annotation.get("checks")
                if summary:
                    item.summary = summary[:300]
                if isinstance(checks, list):
                    clean_checks = [str(value).strip() for value in checks if str(value).strip()]
                    if clean_checks:
                        item.checks = clean_checks[:6]
        position_items = [item for item in items if item.positions]
        if position_items:
            default_overview = (
                f"根据你的画像，从已抓取且详情可核验的公告中整理出 {len(items)} 条相关信息，"
                f"其中 {len(position_items)} 条公告已完成职位表岗位级初核。"
                "岗位结论为初步判断，报名资格仍以招录机关资格审查为准。"
            )
        else:
            default_overview = (
                f"根据你的画像，从已抓取且详情可核验的公告中整理出 {len(items)} 条相关信息。"
                "当前结果是公告级推荐，报名前仍需查看职位表确认专业、学历和身份条件。"
            )
        overview = str(data.get("overview") or "").strip()[:800] or default_overview
        warnings = ["请以官方公告、职位表和招录单位资格审查结果为准。"]
        if not position_items:
            warnings.insert(0, "系统当前尚未完成所有附件的岗位级结构化，不能据此断言一定具备报名资格。")
        elif len(position_items) < len(items):
            warnings.insert(0, "部分公告尚未完成岗位级结构化，未结构化的公告仍需人工核对职位表。")
        if isinstance(data.get("warnings"), list):
            extra = [str(value).strip() for value in data["warnings"] if str(value).strip()]
            warnings = (extra + warnings)[:6]
        if not items:
            overview = "当前画像没有匹配到已抓取且详情可核验的招考公告，请放宽地区或关键词后重试。"
        return overview, warnings


def _infer_exam_types(title: str, source_group: str) -> set[str]:
    result = {name for name, terms in TYPE_TERMS.items() if any(term in title for term in terms)}
    if source_group == "jiangsu_public_college":
        result.add("public_college")
    if not result and source_group in {"jiangsu_province_recruitment", "jiangsu_city_recruitment"}:
        result.add("public_institution")
    if not result and source_group in {"jiangsu_province_hrss", "jiangsu_city_hrss"}:
        result.update({"civil_service", "public_institution"})
    return result


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _select_excerpt(text: str, terms: list[str], limit: int = 360) -> str:
    if not text:
        return ""
    clean_terms = [term for term in terms if term]
    positions = [text.find(term) for term in clean_terms if text.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - 80)
    excerpt = text[start : start + limit]
    if start > 0:
        excerpt = "..." + excerpt
    if start + limit < len(text):
        excerpt += "..."
    return excerpt


def _load_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        result = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in result] if isinstance(result, list) else []


def _fallback_summary(title: str, excerpt: str) -> str:
    if not excerpt:
        return f"发现相关公告：{title}。详情证据文本不足，请直接查看官方原文。"
    return excerpt[:180]


def _default_checks(profile: UserProfile, has_evidence: bool) -> list[str]:
    checks = ["核对职位表中的学历、学位和专业要求", "确认报名时间、方式和所需材料"]
    if profile.graduate_status == "unknown":
        checks.append("确认应届生身份口径")
    if profile.certificates:
        checks.append("核对证书取得时间和岗位要求")
    if not has_evidence:
        checks.append("详情证据不足，需打开官方原文核验")
    return checks


def _show(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"", "unknown", "None"} else text


def _position_model(row: dict[str, Any], evaluation) -> PositionEvaluation:
    reasons = [
        f"{check.label}：{check.requirement} → {check.verdict}"
        for check in evaluation.checks
        if check.requirement not in {"", UNKNOWN}
    ]
    return PositionEvaluation(
        position_id=str(row.get("id") or ""),
        position_code=_show(row.get("position_code")),
        employer=_show(row.get("employer")),
        position_name=_show(row.get("position_name")),
        work_location=_show(row.get("work_location")),
        headcount=_show(row.get("headcount")),
        verdict=evaluation.verdict,
        conditions=[
            ConditionItem(
                field=check.field,
                label=check.label,
                requirement="未列出" if check.requirement in {"", UNKNOWN} else check.requirement,
                verdict=check.verdict,
                reason=check.reason,
            )
            for check in evaluation.checks
        ],
        reasons=reasons[:8],
        questions=evaluation.questions[:4],
    )
