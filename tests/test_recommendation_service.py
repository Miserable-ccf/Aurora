import tempfile
import unittest
from pathlib import Path

from aurora_monitor.db import Database
from aurora_web.llm import LLMClient
from aurora_web.models import UserProfile
from aurora_web.recommendation import RecommendationService
from aurora_web.repository import WebRepository


class RecommendationServiceTests(unittest.TestCase):
    def test_profile_matches_fetched_notice_and_excludes_process_update(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "a.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("p", 1, ["招聘"], [], [])
            db.add_source({
                "id": "s",
                "source_group": "jiangsu_city_recruitment",
                "region_code": "JS-南京",
                "publisher": "南京官方来源",
                "entry_url": "https://example.gov.cn/list",
                "keyword_policy_id": "p",
            })
            with db.transaction() as conn:
                conn.execute(
                    """INSERT INTO notice(id, source_id, title, normalized_title, url,
                       published_at, decision, matched_terms, detail_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("n1", "s", "2026年事业单位公开招聘教师公告", "招聘", "https://example.gov.cn/n1", "2026-03-01", "candidate", '["招聘"]', "fetched"),
                )
                conn.execute(
                    """INSERT INTO evidence_version(id, notice_id, source_url,
                       content_sha256, extracted_text, parser_status)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("e1", "n1", "https://example.gov.cn/n1", "a" * 64, "招聘教师，专业要求计算机科学与技术，报名时间见公告。", "parsed"),
                )
                conn.execute(
                    """INSERT INTO notice(id, source_id, title, normalized_title, url,
                       decision, matched_terms, detail_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("n2", "s", "2026年事业单位招聘成绩公告", "成绩", "https://example.gov.cn/n2", "candidate", '["招聘"]', "fetched"),
                )
                conn.execute(
                    """INSERT INTO evidence_version(id, notice_id, source_url,
                       content_sha256, extracted_text, parser_status)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("e2", "n2", "https://example.gov.cn/n2", "b" * 64, "事业单位招聘成绩公告", "parsed"),
                )
            service = RecommendationService(WebRepository(db), LLMClient())
            result = service.recommend(UserProfile(major="计算机科学与技术", year=2026), save_profile=True)
            self.assertEqual([item.notice_id for item in result.items], ["n1"])
            self.assertFalse(result.llm_used)
            self.assertTrue(result.run_id)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM recommendation_run").fetchone()[0], 1)
            db.close()


if __name__ == "__main__":
    unittest.main()
