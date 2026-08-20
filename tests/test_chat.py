import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aurora_monitor.db import Database
from aurora_web.chat import ChatOrchestrator, normalize_patch, validate_recommendations
from aurora_web.llm import LLMResult
from aurora_web.repository import WebRepository


class _DisabledLLM:
    enabled = False
    model = "fake"
    error = "LLM 尚未配置"

    def chat(self, system, payload, temperature=0.1):
        return LLMResult({}, False, self.model, self.error)

    def chat_with_tools(self, system, payload, tools, executor, max_rounds=4):
        return LLMResult({}, False, self.model, self.error)


class _FakeLLM:
    def __init__(self, extracts=None, selection=None):
        self.enabled = True
        self.model = "fake-model"
        self.extracts = extracts or []
        self.selection = selection
        self.calls = []

    def chat(self, system, payload, temperature=0.1):
        self.calls.append(("chat", payload.get("task", "")[:12]))
        if payload.get("task", "").startswith("判断用户"):
            return LLMResult({"_message": {"content": '{"intent": "confirm"}'}}, True, self.model)
        if self.extracts:
            content = json.dumps({"extracted": self.extracts.pop(0)}, ensure_ascii=False)
            return LLMResult({"_message": {"content": content}}, True, self.model)
        return LLMResult({"_message": {"content": "{}"}}, True, self.model)

    def chat_with_tools(self, system, payload, tools, executor, max_rounds=4):
        self.calls.append(("chat_with_tools", len(tools)))
        if self.selection is None:
            return LLMResult({}, False, self.model, "no selection configured")
        return LLMResult(self.selection, True, self.model)


class _StubService:
    def __init__(self, rows):
        self.rows = rows

    def recommend_positions(self, profile, limit=30):
        return self.rows


def _rows():
    return [
        {"position_id": "p1", "position_code": "01", "employer": "测试学院", "position_name": "专任教师",
         "work_location": "南京", "headcount": "2", "education": "硕士研究生", "degree": "硕士",
         "major_requirement": "计算机类", "verdict": "eligible", "score": 110, "notice_id": "n1",
         "notice_title": "2026年公开招聘公告", "notice_url": "https://example.gov.cn/n1",
         "published_at": "2026-01-01", "match_reasons": ["地区范围匹配"], "questions": ["核对专业方向"]},
        {"position_id": "p2", "position_code": "02", "employer": "测试学院", "position_name": "辅导员",
         "work_location": "南京", "headcount": "1", "education": "本科", "degree": "学士",
         "major_requirement": "不限", "verdict": "eligible", "score": 96, "notice_id": "n1",
         "notice_title": "2026年公开招聘公告", "notice_url": "https://example.gov.cn/n1",
         "published_at": "2026-01-01", "match_reasons": ["地区范围匹配"], "questions": []},
        {"position_id": "p3", "position_code": "03", "employer": "测试学院", "position_name": "实验员",
         "work_location": "南京", "headcount": "1", "education": "本科", "degree": "学士",
         "major_requirement": "计算机类", "verdict": "needs_review", "score": 80, "notice_id": "n1",
         "notice_title": "2026年公开招聘公告", "notice_url": "https://example.gov.cn/n1",
         "published_at": "2026-01-01", "match_reasons": [], "questions": []},
    ]


class NormalizePatchTests(unittest.TestCase):
    def test_exam_labels_and_region_names_are_mapped(self):
        result = normalize_patch({"exam_types": ["事业编", "大专老师", "civil_service"], "region_codes": ["苏州", "江苏全省", "JS-南京"]})
        self.assertEqual(result["exam_types"], ["public_institution", "public_college", "civil_service"])
        self.assertEqual(result["region_codes"], ["JS-苏州", "JS", "JS-南京"])

    def test_unknown_values_are_dropped(self):
        result = normalize_patch({"exam_types": ["外星人"], "region_codes": ["上海"], "major": "  计算机科学与技术  "})
        self.assertEqual(result["exam_types"], [])
        self.assertEqual(result["region_codes"], [])
        self.assertEqual(result["major"], "计算机科学与技术")


class ValidateRecommendationsTests(unittest.TestCase):
    def test_unknown_and_failed_positions_are_rejected(self):
        cards, violations = validate_recommendations(
            _rows(),
            [
                {"position_id": "p-hallucinated", "reason": "编造的岗位"},
                {"position_id": "p1", "reason": "专业匹配"},
                {"position_id": "p1", "reason": "重复"},
            ],
        )
        self.assertEqual([card["position_id"] for card in cards], ["p1"])
        self.assertEqual(len(violations), 2)

    def test_not_eligible_cannot_be_recommended(self):
        rows = _rows()
        rows[1]["verdict"] = "not_eligible"
        cards, violations = validate_recommendations(rows, [{"position_id": "p2"}])
        self.assertEqual(cards, [])
        self.assertEqual(len(violations), 1)


class OrchestratorFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "chat.db")
        self.db.init_schema()
        self.db.seed_regions()
        self.repository = WebRepository(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_rule_mode_full_flow_without_llm(self):
        orchestrator = ChatOrchestrator(self.repository, llm=_DisabledLLM(), service=_StubService(_rows()))
        first = orchestrator.handle("帮我推荐岗位")
        self.assertEqual(first["stage"], "slot_filling")
        self.assertEqual(len(first["missing_fields"]), 5)

        second = orchestrator.handle("", session_id=first["session_id"], profile_patch={
            "exam_types": ["事业编"], "education": "硕士研究生", "degree": "硕士",
            "major": "计算机科学与技术", "region_codes": ["南京"],
        })
        self.assertEqual(second["stage"], "confirm")

        third = orchestrator.handle("确认", session_id=first["session_id"])
        self.assertEqual(third["stage"], "done")
        cards = third["recommendations"]["cards"]
        self.assertEqual(len(cards), 3)
        self.assertTrue(all(card["notice_url"] for card in cards))
        self.assertEqual(cards[0]["position_id"], "p1")

        fourth = orchestrator.handle("还有吗", session_id=first["session_id"])
        self.assertIn("追问模式暂未开放", fourth["reply"])

    def test_slot_filling_requires_all_fields(self):
        orchestrator = ChatOrchestrator(self.repository, llm=_DisabledLLM(), service=_StubService([]))
        first = orchestrator.handle("", profile_patch={"major": "计算机科学与技术"})
        self.assertEqual(first["stage"], "slot_filling")
        self.assertIn("education", first["missing_fields"])

    def test_llm_extraction_and_selection_with_reflection(self):
        llm = _FakeLLM(
            extracts=[{
                "exam_types": ["public_institution"], "education": "硕士研究生", "degree": "硕士",
                "major": "计算机科学与技术", "region_codes": ["JS-南京"],
            }],
            selection={"selected": [
                {"position_id": "p-hallucinated", "reason": "模型编造"},
                {"position_id": "p1", "reason": "专业与学历匹配", "checks": ["核对专业方向"]},
            ]},
        )
        orchestrator = ChatOrchestrator(self.repository, llm=llm, service=_StubService(_rows()))
        first = orchestrator.handle("硕士，计算机，想看南京事业编")
        self.assertEqual(first["stage"], "confirm")

        second = orchestrator.handle("确认", session_id=first["session_id"])
        self.assertEqual(second["stage"], "done")
        recommendation = second["recommendations"]
        self.assertEqual(recommendation["violations"] and len(recommendation["violations"]), 1)
        card_ids = [card["position_id"] for card in recommendation["cards"]]
        self.assertIn("p1", card_ids)
        self.assertNotIn("p-hallucinated", card_ids)
        self.assertEqual(len(card_ids), 3)  # 候补至 3 张卡片

    def test_empty_pool_reports_no_candidates(self):
        orchestrator = ChatOrchestrator(self.repository, llm=_DisabledLLM(), service=_StubService([]))
        first = orchestrator.handle("", profile_patch={
            "exam_types": ["civil_service"], "education": "本科", "degree": "学士",
            "major": "法学", "region_codes": ["JS"],
        })
        second = orchestrator.handle("确认", session_id=first["session_id"])
        self.assertEqual(second["recommendations"]["cards"], [])
        self.assertIn("暂无硬条件初步符合的岗位", second["reply"])


if __name__ == "__main__":
    unittest.main()
