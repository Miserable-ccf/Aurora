"""职位表结构化：把 PDF/XLSX 职位表解析为岗位级记录。

解析只依赖表格本身：先定位表头行（支持合并单元格的双行表头），
按列名映射字段，再从自由文本条件中补充学历、学位、政治面貌等要求。
无法确认的字段保持 ``unknown``，不填默认值；原始行内容随记录保存，
支持后续人工纠错与追溯。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Sequence

from .document_parser import ParsedDocument, parse_document

PARSER_VERSION = 1
UNKNOWN = "unknown"
MAX_SHEET_ROWS = 2000

POSITION_TABLE_TERMS = ("职位表", "岗位表", "岗位简介表", "招聘计划", "职位计划", "招聘岗位表")

# 表头中出现这些词说明是名单/流程表（体检、成绩等），不是招聘职位表
NON_POSITION_HEADER_TERMS = ("姓名", "准考证", "笔试成绩", "面试成绩", "总成绩", "排名", "身份证")

HEADER_FIELDS: dict[str, tuple[str, ...]] = {
    "position_code": ("职位代码", "岗位代码", "职位编码", "岗位编码", "招聘岗位代码", "职位码", "代码"),
    "employer": ("招聘单位", "用人单位", "招录单位", "单位名称", "单位", "部门", "雇主"),
    "position_name": ("岗位名称", "职位名称", "招聘岗位", "岗位简介", "职位简介", "拟聘工作简介", "岗位", "职位"),
    "work_location": ("工作地点", "工作地", "地点", "地区名称", "地区"),
    "headcount": ("招聘人数", "招录人数", "录用人数", "岗位人数", "人数"),
    "education": ("学历要求", "最低学历", "学历"),
    "degree": ("学位要求", "学位"),
    "major_requirement": ("专业要求", "学科专业要求", "专业类别", "专业", "学科"),
    "fresh_graduate_requirement": ("应届要求", "应届毕业生", "面向应届", "应届"),
    "grassroots_requirement": ("基层工作经历", "基层经历", "基层工作年限"),
    "political_requirement": ("政治面貌要求", "政治面貌"),
    "certificate_requirement": ("资格证书", "执业资格", "职业资格", "证书要求", "证书"),
    "age_requirement": ("年龄要求", "年龄条件", "年龄"),
    "gender_requirement": ("性别要求", "性别"),
    "household_requirement": ("户籍要求", "户籍", "生源地"),
    "application_schedule": ("报名与考试时间", "报名时间", "考试时间"),
    "other_requirements": (
        "岗位要求", "招聘条件", "报考条件", "任职条件",
        "招考条件", "岗位招考类别", "招考类别", "岗位类别",
        "其他条件", "其它条件", "其他要求", "其它要求",
        "岗位描述", "职位描述", "开考比例", "备注", "其他",
    ),
}

# 合并单元格父表头允许向右展开的口径（子表头优先）
REQUIREMENT_SPAN_TERMS = ("岗位要求", "招聘条件", "报考条件", "任职条件", "条件", "要求")

CORE_FIELDS = {"position_code", "position_name", "employer"}
STRUCTURED_FIELDS = (
    "position_code", "employer", "position_name", "work_location", "headcount",
    "education", "degree", "major_requirement", "fresh_graduate_requirement",
    "grassroots_requirement", "political_requirement", "certificate_requirement",
    "age_requirement", "gender_requirement", "household_requirement",
    "application_schedule", "other_requirements",
)

UNRESTRICTED_TERMS = {"不限", "无要求", "无限制", "无", "—", "-", "/", "不作要求", "不限制"}


@dataclass
class PositionRecord:
    sheet_name: str = ""
    row_index: int = 0
    position_code: str = UNKNOWN
    employer: str = UNKNOWN
    position_name: str = UNKNOWN
    work_location: str = UNKNOWN
    headcount: str = UNKNOWN
    education: str = UNKNOWN
    degree: str = UNKNOWN
    major_requirement: str = UNKNOWN
    fresh_graduate_requirement: str = UNKNOWN
    grassroots_requirement: str = UNKNOWN
    political_requirement: str = UNKNOWN
    certificate_requirement: str = UNKNOWN
    age_requirement: str = UNKNOWN
    gender_requirement: str = UNKNOWN
    household_requirement: str = UNKNOWN
    application_schedule: str = UNKNOWN
    other_requirements: str = UNKNOWN
    raw_row: list[str] = field(default_factory=list)
    header_row: list[str] = field(default_factory=list)


def looks_like_position_table(title: str, url: str = "") -> bool:
    haystack = re.sub(r"\s+", "", f"{title} {url}")
    return any(term in haystack for term in POSITION_TABLE_TERMS)


def extract_positions(parsed: ParsedDocument, url: str = "") -> list[PositionRecord]:
    """从解析结果中提取岗位记录；XLSX 用行数据，PDF 用文本行近似。"""
    records: list[PositionRecord] = []
    if parsed.sheets:
        for sheet_name, rows in parsed.sheets:
            records.extend(_extract_from_rows(rows, sheet_name))
    elif parsed.rows:
        records.extend(_extract_from_rows(parsed.rows, ""))
    elif url.lower().split("?", 1)[0].endswith(".pdf"):
        records.extend(_extract_from_rows(_rows_from_text(parsed.text), ""))
    return records


def upsert_positions(database, notice_id: str, evidence_id: str, records: Sequence[PositionRecord]) -> int:
    if not records:
        return 0
    columns = ", ".join(STRUCTURED_FIELDS)
    placeholders = ", ".join(["?"] * (len(STRUCTURED_FIELDS) + 8))
    updates = ", ".join(f"{name}=excluded.{name}" for name in STRUCTURED_FIELDS)
    with database.transaction() as conn:
        for record in records:
            position_id = hashlib.sha256(
                f"{evidence_id}:{record.sheet_name}:{record.row_index}".encode("utf-8")
            ).hexdigest()
            conn.execute(
                f"""INSERT INTO position(
                    id, notice_id, evidence_id, sheet_name, row_index, {columns},
                    raw_row, header_row, parser_version)
                    VALUES ({placeholders})
                    ON CONFLICT(evidence_id, sheet_name, row_index) DO UPDATE SET
                    notice_id=excluded.notice_id, {updates},
                    raw_row=excluded.raw_row, header_row=excluded.header_row,
                    parser_version=excluded.parser_version, parsed_at=CURRENT_TIMESTAMP""",
                (
                    position_id, notice_id, evidence_id, record.sheet_name, record.row_index,
                    *[getattr(record, name) for name in STRUCTURED_FIELDS],
                    json.dumps(record.raw_row, ensure_ascii=False),
                    json.dumps(record.header_row, ensure_ascii=False),
                    PARSER_VERSION,
                ),
            )
    return len(records)


def backfill_positions(database, notice_id: str | None = None, limit: int | None = None) -> dict[str, int]:
    """对已保存的证据重新做岗位级解析，用于回补历史数据。"""
    query = """SELECT e.id, e.notice_id, e.source_url, e.content_type
               FROM evidence_version e JOIN notice n ON n.id = e.notice_id
               WHERE e.parser_status IN ('parsed', 'needs_dependency', 'unknown_type', 'error')"""
    params: list = []
    if notice_id:
        query += " AND e.notice_id = ?"
        params.append(notice_id)
    query += " ORDER BY e.retrieved_at"
    if limit:
        query += " LIMIT ?"
        params.append(max(1, int(limit)))
    evidence_count = position_count = 0
    for row in database.connection.execute(query, params).fetchall():
        try:
            body = database.read_evidence(row["id"])
        except (ValueError, OSError):
            continue
        parsed = parse_document(body, row["content_type"] or "", row["source_url"])
        records = extract_positions(parsed, row["source_url"])
        if records:
            evidence_count += 1
            position_count += upsert_positions(database, row["notice_id"], row["id"], records)
    return {"evidence_with_positions": evidence_count, "positions": position_count}


def _normalize_header(cell: str) -> str:
    return re.sub(r"\s+", "", str(cell or ""))


def _map_header(cell: str) -> str | None:
    normalized = _normalize_header(cell)
    if not normalized:
        return None
    if any(term in normalized for term in NON_POSITION_HEADER_TERMS):
        return "exclude"
    if "联系电话" in normalized or "联系方式" in normalized or normalized in {"序号", "编号", "咨询电话"}:
        return "skip"
    for field_name, aliases in HEADER_FIELDS.items():
        if normalized in aliases:
            return field_name
    return None


def _header_hits(row: Sequence[str]) -> int:
    return sum(1 for cell in row if _map_header(cell) not in (None, "skip"))


def _rows_from_text(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "\t" in line:
            cells = [cell.strip() for cell in line.split("\t")]
        else:
            cells = [cell.strip() for cell in re.split(r"\s{2,}", line)]
        if len(cells) >= 3:
            rows.append(cells)
    return rows


def _extract_from_rows(rows: Sequence[Sequence[str]], sheet_name: str) -> list[PositionRecord]:
    records: list[PositionRecord] = []
    header_index = sub_index = None
    for index, row in enumerate(rows[:MAX_SHEET_ROWS]):
        if _header_hits(row) >= 3:
            header_index = index
            if index + 1 < len(rows) and _header_hits(rows[index + 1]) >= 2:
                sub_index = index + 1
            break
    if header_index is None:
        return []
    header_row = list(rows[header_index])
    sub_row = list(rows[sub_index]) if sub_index is not None else None
    if any(_map_header(cell) == "exclude" for cell in header_row + (sub_row or [])):
        return []
    resolved = _resolve_headers(header_row, sub_row)
    if not CORE_FIELDS.intersection(set(resolved)):
        return []
    data_start = (sub_index if sub_index is not None else header_index) + 1
    previous_employer = previous_location = ""
    for index in range(data_start, min(len(rows), MAX_SHEET_ROWS)):
        record = _row_to_record(rows[index], resolved, header_row, sheet_name, index)
        if record is None:
            continue
        if record.employer == UNKNOWN and previous_employer:
            record.employer = previous_employer
        if record.work_location == UNKNOWN and previous_location:
            record.work_location = previous_location
        if record.employer != UNKNOWN:
            previous_employer = record.employer
        if record.work_location != UNKNOWN:
            previous_location = record.work_location
        records.append(record)
    return records


def _resolve_headers(parent_row: Sequence[str], sub_row: Sequence[str] | None) -> list[str | None]:
    width = max(len(parent_row), len(sub_row) if sub_row else 0)
    resolved: list[str | None] = []
    carry_field: str | None = None
    carry_raw = ""
    for index in range(width):
        parent_cell = parent_row[index].strip() if index < len(parent_row) else ""
        sub_cell = sub_row[index].strip() if sub_row and index < len(sub_row) else ""
        parent_field = _map_header(parent_cell)
        sub_field = _map_header(sub_cell)
        if parent_cell:
            carry_field, carry_raw = parent_field, _normalize_header(parent_cell)
        if sub_field in ("skip", "exclude"):
            resolved.append(None)
        elif sub_field:
            resolved.append(sub_field)
        elif parent_field in ("skip", "exclude"):
            resolved.append(None)
        elif parent_field:
            resolved.append(parent_field)
        elif not parent_cell and carry_field == "other_requirements":
            resolved.append("other_requirements")
        elif not parent_cell and carry_field is None and _is_requirement_span(carry_raw):
            resolved.append("other_requirements")
        else:
            resolved.append(None)
    return resolved


def _is_requirement_span(raw_header: str) -> bool:
    return any(term in raw_header for term in REQUIREMENT_SPAN_TERMS)


def _row_to_record(
    row: Sequence[str],
    resolved: Sequence[str | None],
    header_row: Sequence[str],
    sheet_name: str,
    index: int,
) -> PositionRecord | None:
    cells = [str(cell).strip() if cell is not None else "" for cell in row]
    if not any(cells):
        return None
    if _header_hits(cells) >= 3:
        return None
    non_empty = [cell for cell in cells if cell]
    if len(non_empty) <= 1:
        return None
    first = non_empty[0]
    if first.startswith(("备注", "注：", "注:", "说明")):
        return None
    values: dict[str, list[str]] = {}
    for column_index, field_name in enumerate(resolved):
        if not field_name or column_index >= len(cells):
            continue
        cell = cells[column_index]
        if cell:
            values.setdefault(field_name, []).append(cell)
    if not any(values.get(name) for name in CORE_FIELDS):
        return None
    record = PositionRecord(
        sheet_name=sheet_name,
        row_index=index,
        raw_row=cells,
        header_row=[str(cell) for cell in header_row],
    )
    for field_name, parts in values.items():
        setattr(record, field_name, "；".join(dict.fromkeys(parts)))
    _refine_from_free_text(record)
    return record


def _refine_from_free_text(record: PositionRecord) -> None:
    text = record.other_requirements
    if not text or text == UNKNOWN:
        return
    compact = re.sub(r"\s+", "", text)
    segments = [segment for segment in re.split(r"[；;。，,]|(?=\d+[.、])", compact) if segment]

    def find_segment(predicate) -> str:
        for segment in segments:
            if predicate(segment):
                return re.sub(r"^\d+[.、]", "", segment)
        return ""

    if record.education == UNKNOWN:
        match = re.search(r"(博士研究生|硕士研究生|研究生|本科|大专|高职|中专|高中)(及以上|以上|及以下|以下)?", compact)
        if match:
            record.education = match.group(0)
    if record.degree == UNKNOWN:
        segment = find_segment(lambda part: "学位" in part)
        if segment:
            record.degree = segment
    if record.political_requirement == UNKNOWN:
        segment = find_segment(lambda part: "党员" in part)
        if segment:
            record.political_requirement = segment
    if record.fresh_graduate_requirement == UNKNOWN:
        segment = find_segment(
            lambda part: ("应届" in part or re.search(r"20\d{2}年毕业生", part)) and "往届" not in part
        )
        if segment:
            record.fresh_graduate_requirement = segment
    if record.grassroots_requirement == UNKNOWN:
        segment = find_segment(lambda part: "基层" in part and ("经历" in part or "经验" in part or "年限" in part))
        if segment:
            record.grassroots_requirement = segment
    if record.age_requirement == UNKNOWN:
        segment = find_segment(lambda part: "周岁" in part or "年龄" in part)
        if segment:
            record.age_requirement = segment
    if record.gender_requirement == UNKNOWN:
        segment = find_segment(lambda part: "男性" in part or "女性" in part or "适合男" in part or "限男" in part or "限女" in part)
        if segment:
            record.gender_requirement = segment
    if record.household_requirement == UNKNOWN:
        segment = find_segment(lambda part: "户籍" in part or "生源" in part)
        if segment:
            record.household_requirement = segment
    if record.certificate_requirement == UNKNOWN:
        certificate_pattern = re.compile(
            r"教师资格证|法律职业资格|司法考试|执业医师|护士执业|执业药师|注册会计师|建造师|资格证书|执业资格|职业资格"
        )
        segment = find_segment(lambda part: certificate_pattern.search(part) and "毕业证书" not in part)
        if segment:
            record.certificate_requirement = segment
    if record.major_requirement == UNKNOWN:
        segment = find_segment(_looks_like_major)
        if segment:
            record.major_requirement = segment


_MAJOR_NOISE_TERMS = ("知识", "技能", "经验", "能力", "素质", "培训", "沟通", "冷静", "水平", "优先", "熟练", "证书")


def _looks_like_major(part: str) -> bool:
    """自由文本中的专业要求必须形如“XX专业/专业要求/相关专业”，避免把“专业沟通”这类描述误判。"""
    if "专业" not in part or len(part) > 30:
        return False
    if any(noise in part for noise in _MAJOR_NOISE_TERMS):
        return False
    if re.search(r"(专业要求|专业不限|限.{0,6}专业|须.{0,6}专业|专业为|专业：|专业:|专业类)", part):
        return True
    if re.search(r"[\u4e00-\u9fa5]{2,}相关专业", part):
        return True
    return part.endswith("专业") and "、" not in part[:-2] and len(part) <= 12
