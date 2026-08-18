"""从江苏省考试录用公务员专业参考目录 PDF 生成 config/jiangsu-major-catalog.json。

用法：python3 tools/build_major_catalog.py <目录PDF路径> [--output config/jiangsu-major-catalog.json]
需要可选依赖 PyMuPDF（python3 -m pip install PyMuPDF）。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

LEVELS = ("研究生", "本科")
CROSS_REF_PATTERN = re.compile(r"专业大类序号为([\d、]+)的所有专业")


def _split_cell(cell: str) -> tuple[list[str], list[int]]:
    """拆出专业名称与“专业大类序号为N的所有专业”交叉引用。"""
    refs: list[int] = []

    def _collect(match: re.Match) -> str:
        for number in re.findall(r"\d+", match.group(1)):
            refs.append(int(number))
        return ""

    remainder = CROSS_REF_PATTERN.sub(_collect, cell)
    majors = [part.strip() for part in re.split(r"[，、,]", remainder) if part.strip()]
    return majors, refs


def extract_rows(pdf_path: str) -> list[list[str]]:
    import fitz  # type: ignore

    document = fitz.open(pdf_path)
    rows: list[list[str]] = []
    for page in document:
        for table in page.find_tables():
            for row in table.extract():
                cells = [re.sub(r"\s+", "", cell or "") for cell in row]
                rows.append(cells)
    return rows


def build_categories(rows: list[list[str]]) -> list[dict]:
    categories: list[dict] = []
    for cells in rows:
        if len(cells) < 4 or cells[0] == "序号":
            continue
        if cells[0].isdigit():
            categories.append({"name": cells[1], "研究生": cells[2], "本科": cells[3]})
        elif categories:
            previous = categories[-1]
            for level in LEVELS:
                index = 2 if level == "研究生" else 3
                if index < len(cells) and cells[index]:
                    previous[level] = previous[level] + cells[index]
    result = []
    for order, category in enumerate(categories, start=1):
        entry = {"index": order, "name": category["name"], "refs": {}}
        for level in LEVELS:
            majors, refs = _split_cell(category[level])
            entry[level] = list(dict.fromkeys(majors))
            entry["refs"][level] = sorted(set(refs))
        result.append(entry)
    _expand_cross_refs(result)
    return result


def _expand_cross_refs(categories: list[dict]) -> None:
    by_index = {category["index"]: category for category in categories}
    for category in categories:
        for level in LEVELS:
            pending = list(category["refs"].get(level, []))
            seen: set[int] = set()
            while pending:
                ref_index = pending.pop()
                if ref_index in seen or ref_index not in by_index:
                    continue
                seen.add(ref_index)
                target = by_index[ref_index]
                for major in target[level]:
                    if major not in category[level]:
                        category[level].append(major)
                pending.extend(target["refs"].get(level, []))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="专业参考目录 PDF 路径")
    parser.add_argument("--output", default="config/jiangsu-major-catalog.json")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--catalog-name", default="江苏省考试录用公务员专业参考目录")
    args = parser.parse_args()
    categories = build_categories(extract_rows(args.pdf))
    payload = {
        "name": args.catalog_name,
        "source_url": args.source_url,
        "levels": list(LEVELS),
        "categories": categories,
    }
    output = Path(args.output)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(category[level]) for category in categories for level in LEVELS)
    print(json.dumps({"categories": len(categories), "majors": total, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
