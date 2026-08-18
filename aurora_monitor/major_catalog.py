"""江苏省考试录用公务员专业参考目录查询。

目录由 ``tools/build_major_catalog.py`` 从官方 PDF 生成，保存在
``config/jiangsu-major-catalog.json``。类别匹配以目录为准：目录中存在
该类别时给出明确的 符合/不符合 结论，目录未收录的类别回退为待核实。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "jiangsu-major-catalog.json"
LEVELS = ("研究生", "本科")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _strip_paren(value: str) -> str:
    return re.sub(r"[（(][^（）()]*[）)]", "", value)


class MajorCatalog:
    def __init__(self, payload: dict) -> None:
        self.name = str(payload.get("name") or "")
        self.source_url = str(payload.get("source_url") or "")
        self._categories: dict[str, dict] = {}
        self._by_major: dict[str, dict[str, list[str]]] = {level: {} for level in LEVELS}
        for category in payload.get("categories", []):
            name = str(category.get("name") or "")
            if not name:
                continue
            self._categories[name] = category
            for level in LEVELS:
                for major in category.get(level, []):
                    names = self._by_major[level].setdefault(_normalize(major), [])
                    if name not in names:
                        names.append(name)

    def category_names(self) -> list[str]:
        return list(self._categories)

    def has(self, category: str) -> bool:
        return _normalize(category) in self._categories or category in self._categories

    def majors(self, category: str, level: str | None = None) -> set[str]:
        entry = self._categories.get(category) or self._categories.get(_normalize(category))
        if not entry:
            return set()
        levels = [level] if level in LEVELS else list(LEVELS)
        result: set[str] = set()
        for item in levels:
            result.update(entry.get(item, []))
        return result

    def match(self, category: str, major: str, level: str | None = None) -> tuple[bool, str]:
        """返回 (是否属于该类别, 判定依据)。"""
        candidates = {_normalize(item) for item in self.majors(category, level)}
        normalized = _normalize(major)
        if normalized in candidates:
            level_note = f"（{level}层次）" if level in LEVELS else ""
            return True, f"《{self.name}》{level_note}将“{major}”列入“{category}”"
        bare = _strip_paren(normalized)
        if bare:
            for candidate in candidates:
                if bare == _strip_paren(candidate):
                    return True, f"《{self.name}》收录“{candidate}”，与“{major}”去除括注后一致"
        return False, f"《{self.name}》未在“{category}”中收录“{major}”"

    def find_categories(self, major: str, level: str | None = None) -> list[str]:
        normalized = _normalize(major)
        levels = [level] if level in LEVELS else list(LEVELS)
        result: list[str] = []
        for item in levels:
            for category in self._by_major[item].get(normalized, []):
                if category not in result:
                    result.append(category)
        return result


@lru_cache(maxsize=1)
def load_major_catalog(path: str | None = None) -> MajorCatalog | None:
    target = Path(path) if path else DEFAULT_CATALOG_PATH
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return MajorCatalog(payload) if isinstance(payload, dict) else None
