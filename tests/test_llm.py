import json
import os
import unittest
from unittest.mock import patch

from aurora_web.llm import LLMClient
from aurora_web.models import RecommendationItem, UserProfile


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "overview": "模型整理摘要",
                        "items": [{"notice_id": "n1", "summary": "重点查看岗位表", "checks": ["核对专业"]}],
                        "warnings": ["需要复核原文"],
                    }, ensure_ascii=False)
                }
            }]
        }).encode()


class LLMTests(unittest.TestCase):
    def test_openai_compatible_json_response_is_parsed(self):
        item = RecommendationItem(
            notice_id="n1", title="招聘公告", url="https://example.gov.cn/n1",
            publisher="测试来源", region_code="JS", source_group="jiangsu_province_hrss",
            first_seen_at="2026-01-01", detail_status="fetched", score=80,
            match_level="relevant", reasons=["地区匹配"], matched_terms=["招聘"],
            evidence_excerpt="报名时间见公告", summary="旧摘要", checks=["查看原文"],
        )
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://llm.example/v1", "LLM_API_KEY": "k", "LLM_MODEL_NAME": "m"}, clear=False):
            with patch("aurora_web.llm.urlopen", return_value=_FakeResponse()):
                result = LLMClient().organize(UserProfile(), [item])
        self.assertTrue(result.used)
        self.assertEqual(result.data["items"][0]["notice_id"], "n1")


if __name__ == "__main__":
    unittest.main()
