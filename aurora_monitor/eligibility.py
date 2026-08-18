"""岗位级资格判定：逐项比对职位表硬条件与用户画像。

每项硬条件输出 ``符合`` / ``不符合`` / ``待核实``：
- 职位表未列出（unknown）的条件记为“职位表未列出该项要求”，不据此淘汰；
- 画像缺失对应信息时一律 ``待核实`` 并生成核实问题，不做不利推断；
- 专业匹配只依据原文表述：专业名称完整出现才算符合，类别归属需人工核实。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from .major_catalog import load_major_catalog
from .positions import STRUCTURED_FIELDS, UNKNOWN

VERDICT_PASS = "符合"
VERDICT_FAIL = "不符合"
VERDICT_REVIEW = "待核实"

UNRESTRICTED_TERMS = {
    "", "不限", "无要求", "无限制", "不限制", "不作要求", "专业不限",
    "学历不限", "学位不限", "无", "—", "-", "/", "其他人员均可",
}

EDUCATION_RANK = {
    "小学": 0, "初中": 0, "高中": 1, "中专": 1, "中职": 1, "技校": 1,
    "技工院校": 1,
    "专科": 2, "大专": 2, "高职": 2,
    "本科": 3,
    "硕士": 4, "硕士研究生": 4, "研究生": 4,
    "博士": 5, "博士研究生": 5,
}

DEGREE_RANK = {"无学位": 0, "学士": 1, "硕士": 2, "博士": 3}

CHINESE_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass(frozen=True)
class ConditionCheck:
    field: str
    label: str
    requirement: str
    verdict: str
    reason: str


@dataclass
class PositionEvaluation:
    verdict: str  # eligible / needs_review / not_eligible
    checks: list[ConditionCheck] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


def evaluate_position_row(row: Mapping[str, object], profile: object) -> PositionEvaluation:
    values = {name: _clean(row.get(name)) for name in STRUCTURED_FIELDS}
    checks: list[ConditionCheck] = []
    checks.append(_check_education(values["education"], _attr(profile, "education")))
    checks.append(_check_degree(values["degree"], _attr(profile, "degree")))
    checks.append(_check_major(values["major_requirement"], _attr(profile, "major"), _attr(profile, "education")))
    checks.append(_check_fresh_graduate(values["fresh_graduate_requirement"], _attr(profile, "graduate_status"), _attr(profile, "year")))
    checks.append(_check_political(values["political_requirement"], _attr(profile, "political_status")))
    checks.append(_check_grassroots(values["grassroots_requirement"], _attr(profile, "grassroots_years")))
    checks.append(_check_certificates(values["certificate_requirement"], _attr(profile, "certificates") or []))
    checks.append(_check_unmodeled("age_requirement", "年龄", values["age_requirement"], "请核对本人出生日期是否满足职位表年龄要求"))
    checks.append(_check_unmodeled("gender_requirement", "性别", values["gender_requirement"], "请确认本人性别是否符合职位表要求"))
    checks.append(_check_unmodeled("household_requirement", "户籍/生源地", values["household_requirement"], "请确认本人户籍或生源地是否符合职位表要求"))
    work_experience = _find_work_experience(values)
    if work_experience:
        checks.append(ConditionCheck(
            "other_requirements", "工作经历", work_experience, VERDICT_REVIEW,
            f"职位表要求“{work_experience}”，画像未提供工作年限，需自行核对",
        ))
    questions = [check.reason for check in checks if check.verdict == VERDICT_REVIEW]
    if any(check.verdict == VERDICT_FAIL for check in checks):
        overall = "not_eligible"
    elif questions:
        overall = "needs_review"
    else:
        overall = "eligible"
    return PositionEvaluation(verdict=overall, checks=checks, questions=questions)


def _attr(profile: object, name: str):
    return getattr(profile, name, None)


def _clean(value: object) -> str:
    text = str(value or "").strip()
    return text if text and text.lower() != "none" else UNKNOWN


def _is_unrestricted(value: str) -> bool:
    return re.sub(r"\s+", "", value) in UNRESTRICTED_TERMS


def _missing(field_name: str, label: str, requirement: str) -> ConditionCheck:
    return ConditionCheck(field_name, label, requirement, VERDICT_PASS, "职位表未列出该项要求")


def _education_rank(text: str) -> int | None:
    compact = re.sub(r"\s+", "", text)
    for name in sorted(EDUCATION_RANK, key=len, reverse=True):
        if name in compact:
            return EDUCATION_RANK[name]
    return None


def _degree_rank(text: str) -> int | None:
    compact = re.sub(r"\s+", "", text)
    for name in sorted(DEGREE_RANK, key=len, reverse=True):
        if name in compact:
            return DEGREE_RANK[name]
    return None


def _check_education(requirement: str, user_education: str | None) -> ConditionCheck:
    label = "学历"
    if requirement == UNKNOWN:
        return _missing("education", label, requirement)
    if _is_unrestricted(requirement):
        return ConditionCheck("education", label, requirement, VERDICT_PASS, "职位表未限制学历")
    user_education = str(user_education or "").strip()
    if not user_education:
        return ConditionCheck("education", label, requirement, VERDICT_REVIEW, f"职位表要求学历“{requirement}”，你未填写学历")
    required_rank = _education_rank(requirement)
    user_rank = _education_rank(user_education)
    if required_rank is None:
        return ConditionCheck("education", label, requirement, VERDICT_REVIEW, f"无法解析学历要求“{requirement}”，请人工核对")
    if user_rank is None:
        return ConditionCheck("education", label, requirement, VERDICT_REVIEW, f"无法识别你的学历“{user_education}”与要求“{requirement}”的关系")
    compact = re.sub(r"\s+", "", requirement)
    at_most = "及以下" in compact or "以下" in compact
    at_least = "及以上" in compact or "以上" in compact
    if at_most:
        if user_rank <= required_rank:
            return ConditionCheck("education", label, requirement, VERDICT_PASS, f"你的学历“{user_education}”满足“{requirement}”")
        return ConditionCheck("education", label, requirement, VERDICT_FAIL, f"职位要求“{requirement}”，你的学历“{user_education}”超出范围")
    if not at_least and user_rank != required_rank:
        if user_rank > required_rank:
            return ConditionCheck("education", label, requirement, VERDICT_REVIEW, f"职位要求“{requirement}”，你的学历更高，需确认是否仅限该学历报考")
        return ConditionCheck("education", label, requirement, VERDICT_FAIL, f"职位要求“{requirement}”，你的学历“{user_education}”未达到")
    if user_rank >= required_rank:
        return ConditionCheck("education", label, requirement, VERDICT_PASS, f"你的学历“{user_education}”满足“{requirement}”")
    return ConditionCheck("education", label, requirement, VERDICT_FAIL, f"职位要求“{requirement}”，你的学历“{user_education}”未达到")


def _check_degree(requirement: str, user_degree: str | None) -> ConditionCheck:
    label = "学位"
    if requirement == UNKNOWN:
        return _missing("degree", label, requirement)
    if _is_unrestricted(requirement):
        return ConditionCheck("degree", label, requirement, VERDICT_PASS, "职位表未限制学位")
    user_degree = str(user_degree or "").strip()
    if "相应学位" in re.sub(r"\s+", "", requirement) or "学位" in requirement and _degree_rank(requirement) is None:
        if not user_degree:
            return ConditionCheck("degree", label, requirement, VERDICT_REVIEW, f"职位表要求“{requirement}”，你未填写学位")
        if user_degree == "无学位":
            return ConditionCheck("degree", label, requirement, VERDICT_FAIL, f"职位表要求“{requirement}”，你未填写学位信息为“无学位”")
        return ConditionCheck("degree", label, requirement, VERDICT_PASS, f"你已填写学位“{user_degree}”，满足“{requirement}”")
    required_rank = _degree_rank(requirement)
    user_rank = _degree_rank(user_degree) if user_degree else None
    if required_rank is None:
        return ConditionCheck("degree", label, requirement, VERDICT_REVIEW, f"无法解析学位要求“{requirement}”，请人工核对")
    if user_rank is None:
        return ConditionCheck("degree", label, requirement, VERDICT_REVIEW, f"职位表要求学位“{requirement}”，你未填写学位")
    if user_rank >= required_rank:
        return ConditionCheck("degree", label, requirement, VERDICT_PASS, f"你的学位“{user_degree}”满足“{requirement}”")
    return ConditionCheck("degree", label, requirement, VERDICT_FAIL, f"职位要求学位“{requirement}”，你的学位“{user_degree}”未达到")


def _education_level(education: str | None) -> str | None:
    compact = re.sub(r"\s+", "", str(education or ""))
    if any(term in compact for term in ("硕士", "研究生", "博士")):
        return "研究生"
    if "本科" in compact:
        return "本科"
    return None


def _check_major(requirement: str, user_major: str | None, education: str | None = None) -> ConditionCheck:
    label = "专业"
    if requirement == UNKNOWN:
        return _missing("major_requirement", label, requirement)
    if _is_unrestricted(requirement):
        return ConditionCheck("major_requirement", label, requirement, VERDICT_PASS, "职位表未限制专业")
    user_major = str(user_major or "").strip()
    if not user_major:
        return ConditionCheck("major_requirement", label, requirement, VERDICT_REVIEW, f"职位表要求专业“{requirement}”，你未填写专业")
    user_compact = re.sub(r"\s+", "", user_major)
    alternatives = [part for part in re.split(r"[；;、,，/]|或", requirement) if part.strip()]
    category_maybe: list[str] = []
    catalog_fail: list[str] = []
    catalog = load_major_catalog()
    level = _education_level(education)
    for alternative in alternatives:
        alternative_compact = re.sub(r"\s+", "", alternative)
        if not alternative_compact or _is_unrestricted(alternative_compact):
            return ConditionCheck("major_requirement", label, requirement, VERDICT_PASS, "职位表未限制专业")
        if user_compact == alternative_compact or user_compact in alternative_compact:
            return ConditionCheck("major_requirement", label, requirement, VERDICT_PASS, f"原文专业要求包含你的专业“{user_major}”")
        if len(alternative_compact) >= 2 and alternative_compact in user_compact:
            category_maybe.append(alternative_compact)
            continue
        if alternative_compact.endswith("类"):
            if catalog and catalog.has(alternative_compact):
                belongs, basis = catalog.match(alternative_compact, user_major, level)
                if belongs:
                    return ConditionCheck("major_requirement", label, requirement, VERDICT_PASS, basis)
                if level is not None:
                    catalog_fail.append(basis)
                else:
                    category_maybe.append(alternative_compact)
                continue
            stem = alternative_compact[:-1]
            bare_stem = re.sub(r"[（(][^）)]*[）)]", "", stem)
            if stem and (stem in user_compact or (bare_stem and bare_stem in user_compact)):
                category_maybe.append(alternative_compact)
    if category_maybe:
        return ConditionCheck(
            "major_requirement", label, requirement, VERDICT_REVIEW,
            f"你的专业“{user_major}”可能属于“{'、'.join(category_maybe)}”，请按专业目录向招录单位确认",
        )
    if catalog_fail:
        return ConditionCheck(
            "major_requirement", label, requirement, VERDICT_FAIL,
            f"{catalog_fail[0]}（如目录收录有出入，请以招录单位解释为准）",
        )
    return ConditionCheck("major_requirement", label, requirement, VERDICT_FAIL, f"职位要求专业“{requirement}”，你的专业“{user_major}”未体现符合")


def _check_fresh_graduate(requirement: str, graduate_status: str | None, year: int | None = None) -> ConditionCheck:
    label = "应届身份"
    if requirement == UNKNOWN:
        return _missing("fresh_graduate_requirement", label, requirement)
    if _is_unrestricted(requirement):
        return ConditionCheck("fresh_graduate_requirement", label, requirement, VERDICT_PASS, "职位表未限制应届身份")
    compact = re.sub(r"\s+", "", requirement)
    year_match = re.search(r"(20\d{2})年毕业生", compact)
    if "应届" not in compact and not year_match:
        return ConditionCheck("fresh_graduate_requirement", label, requirement, VERDICT_REVIEW, f"无法解析应届要求“{requirement}”，请人工核对")
    if year_match and year is not None and int(year_match.group(1)) != int(year):
        return ConditionCheck("fresh_graduate_requirement", label, requirement, VERDICT_FAIL, f"职位要求“{requirement}”，你的目标年份为 {year}")
    status = str(graduate_status or "unknown").strip()
    if status == "fresh":
        return ConditionCheck("fresh_graduate_requirement", label, requirement, VERDICT_PASS, f"你为应届身份，满足“{requirement}”")
    if status == "non_fresh":
        return ConditionCheck("fresh_graduate_requirement", label, requirement, VERDICT_FAIL, f"职位要求“{requirement}”，你为非应届身份")
    return ConditionCheck("fresh_graduate_requirement", label, requirement, VERDICT_REVIEW, f"职位要求“{requirement}”，你的应届身份待确认")


def _check_political(requirement: str, political_status: str | None) -> ConditionCheck:
    label = "政治面貌"
    if requirement == UNKNOWN:
        return _missing("political_requirement", label, requirement)
    if _is_unrestricted(requirement):
        return ConditionCheck("political_requirement", label, requirement, VERDICT_PASS, "职位表未限制政治面貌")
    compact = re.sub(r"\s+", "", requirement)
    if "党员" not in compact:
        return ConditionCheck("political_requirement", label, requirement, VERDICT_REVIEW, f"无法解析政治面貌要求“{requirement}”，请人工核对")
    status = str(political_status or "").strip()
    if "党员" in status:
        return ConditionCheck("political_requirement", label, requirement, VERDICT_PASS, f"你的政治面貌“{status}”满足“{requirement}”")
    if not status:
        return ConditionCheck("political_requirement", label, requirement, VERDICT_REVIEW, f"职位要求“{requirement}”，你未填写政治面貌")
    return ConditionCheck("political_requirement", label, requirement, VERDICT_FAIL, f"职位要求“{requirement}”，你的政治面貌为“{status}”")


def _parse_years(text: str) -> int | None:
    match = re.search(r"(\d+)\s*年", text)
    if match:
        return int(match.group(1))
    match = re.search(r"([一二两三四五六七八九十])\s*年", text)
    if match:
        return CHINESE_NUMBERS.get(match.group(1))
    return None


def _check_grassroots(requirement: str, grassroots_years: int | None) -> ConditionCheck:
    label = "基层经历"
    if requirement == UNKNOWN:
        return _missing("grassroots_requirement", label, requirement)
    if _is_unrestricted(requirement):
        return ConditionCheck("grassroots_requirement", label, requirement, VERDICT_PASS, "职位表未限制基层经历")
    compact = re.sub(r"\s+", "", requirement)
    years = _parse_years(compact)
    if years is None:
        return ConditionCheck("grassroots_requirement", label, requirement, VERDICT_REVIEW, f"无法解析基层经历要求“{requirement}”，请人工核对")
    if grassroots_years is None:
        return ConditionCheck("grassroots_requirement", label, requirement, VERDICT_REVIEW, f"职位要求“{requirement}”，你未填写基层经历年限")
    if int(grassroots_years) >= years:
        return ConditionCheck("grassroots_requirement", label, requirement, VERDICT_PASS, f"你的基层经历 {grassroots_years} 年满足“{requirement}”")
    return ConditionCheck("grassroots_requirement", label, requirement, VERDICT_FAIL, f"职位要求“{requirement}”，你的基层经历为 {grassroots_years} 年")


def _check_certificates(requirement: str, certificates: list) -> ConditionCheck:
    label = "资格证书"
    if requirement == UNKNOWN:
        return _missing("certificate_requirement", label, requirement)
    if _is_unrestricted(requirement):
        return ConditionCheck("certificate_requirement", label, requirement, VERDICT_PASS, "职位表未要求资格证书")
    items = [part.strip() for part in re.split(r"[；;、,，。]", requirement) if part.strip()]
    if not items:
        return ConditionCheck("certificate_requirement", label, requirement, VERDICT_REVIEW, f"无法解析证书要求“{requirement}”，请人工核对")
    provided = [str(cert).strip() for cert in certificates if str(cert).strip()]
    if not provided:
        return ConditionCheck("certificate_requirement", label, requirement, VERDICT_REVIEW, f"职位表要求证书“{requirement}”，你未填写任何证书")
    missing = [item for item in items if not any(item in cert or cert in item for cert in provided)]
    if not missing:
        return ConditionCheck("certificate_requirement", label, requirement, VERDICT_PASS, f"你填写的证书覆盖“{requirement}”")
    return ConditionCheck("certificate_requirement", label, requirement, VERDICT_FAIL, f"你填写的证书未包含“{'、'.join(missing)}”")


def _check_unmodeled(field_name: str, label: str, requirement: str, question: str) -> ConditionCheck:
    if requirement == UNKNOWN:
        return _missing(field_name, label, requirement)
    if _is_unrestricted(requirement):
        return ConditionCheck(field_name, label, requirement, VERDICT_PASS, f"职位表未限制{label}")
    return ConditionCheck(field_name, label, requirement, VERDICT_REVIEW, f"职位表要求“{requirement}”，{question}")


def _find_work_experience(values: Mapping[str, str]) -> str:
    haystack = "；".join(
        value for value in (values["grassroots_requirement"], values["other_requirements"]) if value != UNKNOWN
    )
    if "基层" in haystack:
        return ""
    match = re.search(r"(\d+|[一二两三四五六七八九十])\s*年(?:及以上|以上|以下)?(?:相关)?(?:工作(?:经历|经验)|基层(?:经历|经验))", re.sub(r"\s+", "", haystack))
    return match.group(0) if match else ""
