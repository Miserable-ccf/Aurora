import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aurora_monitor.db import Database
from aurora_web.chat import (
    FOLLOWUP_GUIDANCE,
    ChatOrchestrator,
    _extract_position_ref,
    _to_ordinal,
    normalize_patch,
    validate_recommendations,
)
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
    def __init__(self, extracts=None, selection=None, followups=None, answer=None):
        self.enabled = True
        self.model = "fake-model"
        self.extracts = extracts or []
        self.selection = selection
        self.followups = followups or []
        self.answer = answer
        self.calls = []

    def chat(self, system, payload, temperature=0.1):
        self.calls.append(("chat", payload.get("task", "")[:12]))
        task = payload.get("task", "")
        if task.startswith("以下是按资格核验"):
            if self.selection is None:
                return LLMResult({}, False, self.model, "no selection configured")
            return LLMResult(self.selection, True, self.model)
        if task.startswith("判断用户在岗位推荐完成后"):
            if self.followups:
                content = json.dumps(self.followups.pop(0), ensure_ascii=False)
                return LLMResult({"_message": {"content": content}}, True, self.model)
            return LLMResult({"_message": {"content": '{"intent": "general", "position_ref": null}'}}, True, self.model)
        if task.startswith("判断用户"):
            return LLMResult({"_message": {"content": '{"intent": "confirm"}'}}, True, self.model)
        if self.extracts:
            content = json.dumps({"extracted": self.extracts.pop(0)}, ensure_ascii=False)
            return LLMResult({"_message": {"content": content}}, True, self.model)
        return LLMResult({"_message": {"content": "{}"}}, True, self.model)

    def chat_with_tools(self, system, payload, tools, executor, max_rounds=4):
        self.calls.append(("chat_with_tools", len(tools)))
        if str(payload.get("task", "")).startswith("用户在岗位推荐完成后提出追问"):
            if self.answer is None:
                return LLMResult({}, False, self.model, "no answer configured")
            return LLMResult({"answer": self.answer}, True, self.model)
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

    def test_exclude_keywords_normalized(self):
        result = normalize_patch({"exclude_keywords": [" 辅导员 ", "", "行政管理", "辅导员"]})
        self.assertEqual(result["exclude_keywords"], ["辅导员", "行政管理"])


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

    def test_string_checks_is_wrapped_not_split_into_chars(self):
        cards, violations = validate_recommendations(_rows(), [{"position_id": "p1", "reason": "ok", "checks": "需核对公告原文"}])
        self.assertEqual(cards[0]["checks"], ["需核对公告原文"])
        self.assertEqual(violations, [])

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
        self.assertEqual(fourth["stage"], "followup")
        self.assertIn("暂无更多", fourth["reply"])

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


def _row(position_id, name, verdict="eligible", score=100):
    return {"position_id": position_id, "position_code": position_id, "employer": "测试学院", "position_name": name,
            "work_location": "南京", "headcount": "1", "education": "硕士研究生", "degree": "硕士",
            "major_requirement": "计算机类", "verdict": verdict, "score": score, "notice_id": "n1",
            "notice_title": "2026年公开招聘公告", "notice_url": "https://example.gov.cn/n1",
            "published_at": "2026-01-01", "match_reasons": ["地区范围匹配"], "questions": ["核对专业方向"]}


class FollowupHelpersTests(unittest.TestCase):
    def test_position_ref_parsing(self):
        self.assertEqual(_extract_position_ref("第一个岗位的详情"), 1)
        self.assertEqual(_extract_position_ref("岗位2的要求"), 2)
        self.assertEqual(_extract_position_ref("3号具体什么情况"), 3)
        self.assertEqual(_extract_position_ref("第 三个"), 3)
        self.assertIsNone(_extract_position_ref("还有更多吗"))
        self.assertEqual(_to_ordinal("2"), 2)
        self.assertEqual(_to_ordinal("三"), 3)
        self.assertIsNone(_to_ordinal(5))
        self.assertIsNone(_to_ordinal(None))


def _flow_to_done(orchestrator):
    first = orchestrator.handle("", profile_patch={
        "exam_types": ["事业编"], "education": "硕士研究生", "degree": "硕士",
        "major": "计算机科学与技术", "region_codes": ["南京"],
    })
    second = orchestrator.handle("确认", session_id=first["session_id"])
    assert second["stage"] == "done"
    return second


class FollowupFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "chat.db")
        self.db.init_schema()
        self.db.seed_regions()
        self.repository = WebRepository(self.db)
        self.db.upsert_policy("p", 1, ["招聘"], [], [])
        self.db.add_source({
            "id": "s", "source_group": "test", "region_code": "JS-南京",
            "publisher": "测试来源", "entry_url": "https://example.gov.cn/list",
            "keyword_policy_id": "p",
        })
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO notice(id, source_id, title, normalized_title, url,
                   published_at, decision, matched_terms, detail_status)
                   VALUES ('n1', 's', '2026年公开招聘公告', '招聘', 'https://example.gov.cn/n1',
                   '2026-01-01', 'candidate', '["招聘"]', 'fetched')"""
            )
            conn.execute(
                """INSERT INTO evidence_version(id, notice_id, source_url, content_sha256, parser_status)
                   VALUES ('e1', 'n1', 'https://example.gov.cn/a.xlsx', ?, 'parsed')""",
                ("a" * 64,),
            )
            for row_index, (position_id, position_name) in enumerate((("p1", "专任教师"), ("p2", "辅导员"), ("p3", "实验员")), start=1):
                conn.execute(
                    """INSERT INTO position(id, notice_id, evidence_id, sheet_name, row_index,
                       position_code, employer, position_name, education, degree, major_requirement)
                       VALUES (?, 'n1', 'e1', 'Sheet1', ?, '01', '测试学院', ?, '硕士研究生', '硕士', '计算机类')""",
                    (position_id, row_index, position_name),
                )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _orchestrator(self, llm, rows):
        return ChatOrchestrator(self.repository, llm=llm, service=_StubService(rows))

    def test_detail_by_ordinal_in_rule_mode(self):
        orchestrator = self._orchestrator(_DisabledLLM(), _rows())
        done = _flow_to_done(orchestrator)
        first_id = done["recommendations"]["cards"][0]["position_id"]
        reply = orchestrator.handle("第一个岗位的详情", session_id=done["session_id"])
        self.assertEqual(reply["stage"], "followup")
        self.assertIn("专任教师", reply["reply"])
        self.assertIn("来源公告", reply["reply"])
        self.assertIn("逐条核验", reply["reply"])
        self.assertEqual(first_id, "p1")

    def test_detail_without_ordinal_asks_clarification(self):
        orchestrator = self._orchestrator(_DisabledLLM(), _rows())
        done = _flow_to_done(orchestrator)
        reply = orchestrator.handle("详情", session_id=done["session_id"])
        self.assertIn("第几个", reply["reply"])

    def test_more_positions_paginates_and_exhausts(self):
        rows = [_row("p1", "岗位一"), _row("p2", "岗位二"), _row("p3", "岗位三"), _row("p4", "岗位四"), _row("p5", "岗位五")]
        orchestrator = self._orchestrator(_DisabledLLM(), rows)
        done = _flow_to_done(orchestrator)
        shown = {card["position_id"] for card in done["recommendations"]["cards"]}
        self.assertEqual(len(shown), 3)

        more = orchestrator.handle("还有更多岗位吗", session_id=done["session_id"])
        self.assertEqual(more["stage"], "followup")
        new_cards = more["recommendations"]["cards"]
        self.assertEqual(len(new_cards), 2)
        self.assertFalse(shown & {card["position_id"] for card in new_cards})

        exhausted = orchestrator.handle("还有吗", session_id=done["session_id"])
        self.assertIn("暂无更多", exhausted["reply"])

    def test_modify_profile_recommends_again_with_llm(self):
        llm = _FakeLLM(
            extracts=[{"region_codes": ["JS"]}],
            followups=[{"intent": "modify_profile", "position_ref": None}],
        )
        orchestrator = self._orchestrator(llm, _rows())
        done = _flow_to_done(orchestrator)
        reply = orchestrator.handle("地区改成江苏全省", session_id=done["session_id"])
        self.assertEqual(reply["stage"], "followup")
        self.assertIn("已按新条件重新推荐", reply["reply"])
        self.assertEqual(reply["profile_draft"]["region_codes"], ["JS"])
        self.assertIn("recommendations", reply)

    def test_modify_without_llm_explains_limitation(self):
        orchestrator = self._orchestrator(_DisabledLLM(), _rows())
        done = _flow_to_done(orchestrator)
        reply = orchestrator.handle("重新改一下地区", session_id=done["session_id"])
        self.assertIn("未启用 LLM", reply["reply"])

    def test_general_question_with_llm_answer(self):
        llm = _FakeLLM(followups=[{"intent": "general", "position_ref": None}], answer="事业编与公务员的编制性质不同，请以公告为准。")
        orchestrator = self._orchestrator(llm, _rows())
        done = _flow_to_done(orchestrator)
        reply = orchestrator.handle("事业编和公务员有什么区别", session_id=done["session_id"])
        self.assertEqual(reply["stage"], "followup")
        self.assertIn("编制性质不同", reply["reply"])

    def test_general_question_without_llm_gives_guidance(self):
        orchestrator = self._orchestrator(_DisabledLLM(), _rows())
        done = _flow_to_done(orchestrator)
        reply = orchestrator.handle("事业编和公务员有什么区别", session_id=done["session_id"])
        self.assertEqual(reply["reply"], FOLLOWUP_GUIDANCE)

    def test_history_restores_messages_with_cards(self):
        orchestrator = self._orchestrator(_DisabledLLM(), _rows())
        done = _flow_to_done(orchestrator)
        orchestrator.handle("第一个岗位的详情", session_id=done["session_id"])

        history = orchestrator.history(done["session_id"])
        self.assertTrue(history["found"])
        self.assertEqual(history["stage"], "followup")
        roles = [message["role"] for message in history["messages"]]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        card_messages = [message for message in history["messages"] if message["cards"]]
        self.assertEqual(len(card_messages), 1)
        self.assertEqual(len(card_messages[0]["cards"]), 3)
        self.assertTrue(all(card["notice_url"] for card in card_messages[0]["cards"]))

        # 模拟重启后用同一 store 重建编排器（会话持久化在库里）
        reborn = ChatOrchestrator(self.repository, llm=_DisabledLLM(), service=_StubService(_rows()))
        history2 = reborn.history(done["session_id"])
        self.assertTrue(history2["found"])
        self.assertEqual(len(history2["messages"]), len(history["messages"]))

    def test_modify_adds_exclusion_and_profile_shows_it(self):
        llm = _FakeLLM(
            extracts=[{"exclude_keywords": ["辅导员"]}],
            followups=[{"intent": "modify_profile", "position_ref": None}],
        )
        orchestrator = self._orchestrator(llm, _rows())
        done = _flow_to_done(orchestrator)
        reply = orchestrator.handle("我不要辅导员岗位，要教师岗", session_id=done["session_id"])
        self.assertEqual(reply["stage"], "followup")
        self.assertEqual(reply["profile_draft"]["exclude_keywords"], ["辅导员"])
        self.assertIn("排除岗位", reply["reply"])

    def test_exclusions_accumulate_when_modified_twice(self):
        llm = _FakeLLM(
            extracts=[{"exclude_keywords": ["辅导员"]}, {"exclude_keywords": ["行政管理"]}],
            followups=[{"intent": "modify_profile", "position_ref": None}, {"intent": "modify_profile", "position_ref": None}],
        )
        orchestrator = self._orchestrator(llm, _rows())
        done = _flow_to_done(orchestrator)
        orchestrator.handle("不要辅导员", session_id=done["session_id"])
        reply = orchestrator.handle("行政管理也不要", session_id=done["session_id"])
        self.assertEqual(reply["profile_draft"]["exclude_keywords"], ["辅导员", "行政管理"])

    def test_rule_mode_classifies_exclusion_request_as_modify(self):
        orchestrator = self._orchestrator(_DisabledLLM(), _rows())
        done = _flow_to_done(orchestrator)
        reply = orchestrator.handle("不要辅导员", session_id=done["session_id"])
        self.assertIn("未启用 LLM", reply["reply"])

    def test_history_unknown_session(self):
        orchestrator = self._orchestrator(_DisabledLLM(), _rows())
        history = orchestrator.history("不存在的会话")
        self.assertFalse(history["found"])
        self.assertEqual(history["messages"], [])
