import tempfile
import unittest
import json
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

    def test_jobfair_notices_are_never_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "a.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("p", 1, ["招聘"], [], [])
            db.add_source({
                "id": "s",
                "source_group": "jiangsu_city_hrss",
                "region_code": "JS-苏州",
                "publisher": "苏州人社",
                "entry_url": "https://example.gov.cn/list",
                "keyword_policy_id": "p",
            })
            with db.transaction() as conn:
                conn.execute(
                    """INSERT INTO notice(id, source_id, title, normalized_title, url,
                       published_at, decision, matched_terms, detail_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("n1", "s", "2026年春风行动暨就业援助季系列招聘活动岗位明细", "招聘", "https://example.gov.cn/n1", "2026-03-01", "candidate", '["招聘"]', "fetched"),
                )
                conn.execute(
                    """INSERT INTO evidence_version(id, notice_id, source_url, content_sha256, parser_status)
                       VALUES ('e1', 'n1', 'https://example.gov.cn/a.xlsx', ?, 'parsed')""",
                    ("a" * 64,),
                )
                conn.execute(
                    """INSERT INTO position(id, notice_id, evidence_id, sheet_name, row_index,
                       position_code, employer, position_name, education, major_requirement)
                       VALUES ('p1', 'n1', 'e1', 'Sheet1', 1, '01', '某公司', '仓管员', '不限', '不限')"""
                )
            service = RecommendationService(WebRepository(db), LLMClient())
            profile = UserProfile(exam_types=["civil_service", "public_institution"], region_codes=["JS"])
            self.assertEqual(service.recommend_positions(profile), [])
            db.close()

    def test_recommend_positions_excludes_by_position_name_and_notice_title(self):
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
                    ("n1", "s", "2026年事业单位公开招聘工作人员公告", "招聘", "https://example.gov.cn/n1", "2026-03-01", "candidate", '["招聘"]', "fetched"),
                )
                conn.execute(
                    """INSERT INTO evidence_version(id, notice_id, source_url, content_sha256, parser_status)
                       VALUES ('e1', 'n1', 'https://example.gov.cn/a.xlsx', ?, 'parsed')""",
                    ("a" * 64,),
                )
                conn.execute(
                    """INSERT INTO position(id, notice_id, evidence_id, sheet_name, row_index,
                       position_code, employer, position_name, education, major_requirement)
                       VALUES ('p1', 'n1', 'e1', 'Sheet1', 1, '01', '测试学院', '专任教师', '硕士研究生及以上', '计算机科学与技术')"""
                )
                conn.execute(
                    """INSERT INTO position(id, notice_id, evidence_id, sheet_name, row_index,
                       position_code, employer, position_name, education, major_requirement)
                       VALUES ('p2', 'n1', 'e1', 'Sheet1', 2, '02', '测试学院', '专职辅导员', '硕士研究生及以上', '不限')"""
                )
                conn.execute(
                    """INSERT INTO notice(id, source_id, title, normalized_title, url,
                       published_at, decision, matched_terms, detail_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("n2", "s", "2026年事业单位公开招聘辅导员公告", "招聘", "https://example.gov.cn/n2", "2026-03-02", "candidate", '["招聘"]', "fetched"),
                )
                conn.execute(
                    """INSERT INTO evidence_version(id, notice_id, source_url, content_sha256, parser_status)
                       VALUES ('e2', 'n2', 'https://example.gov.cn/b.xlsx', ?, 'parsed')""",
                    ("b" * 64,),
                )
                conn.execute(
                    """INSERT INTO position(id, notice_id, evidence_id, sheet_name, row_index,
                       position_code, employer, position_name, education, major_requirement)
                       VALUES ('p3', 'n2', 'e1', 'Sheet1', 3, '01', '测试学院', '专职辅导员', '硕士研究生及以上', '不限')"""
                )
            service = RecommendationService(WebRepository(db), LLMClient())
            base = dict(major="计算机科学与技术", education="硕士研究生", degree="硕士", region_codes=["JS-南京"])
            # 排除“辅导员”：混招公告保留专任教师，辅导员专场公告整篇跳过
            rows = service.recommend_positions(UserProfile(exclude_keywords=["辅导员"], **base))
            self.assertEqual([row["position_name"] for row in rows], ["专任教师"])
            # 不排除时三个岗位都在候选里
            rows_all = service.recommend_positions(UserProfile(**base))
            self.assertEqual(len(rows_all), 3)
            db.close()

    def test_position_level_evaluation_marks_eligible_and_excludes_failed(self):
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
                    """INSERT INTO evidence_version(id, notice_id, source_url, content_sha256, parser_status)
                       VALUES ('e1', 'n1', 'https://example.gov.cn/a.xlsx', ?, 'parsed')""",
                    ("a" * 64,),
                )
                conn.execute(
                    """INSERT INTO position(id, notice_id, evidence_id, sheet_name, row_index,
                       position_code, employer, position_name, education, major_requirement)
                       VALUES ('p1', 'n1', 'e1', 'Sheet1', 3, '01', '测试学校', '计算机教师', '本科及以上', '计算机科学与技术')"""
                )
                conn.execute(
                    """INSERT INTO notice(id, source_id, title, normalized_title, url,
                       published_at, decision, matched_terms, detail_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("n2", "s", "2026年事业单位高层次人才招聘公告", "招聘", "https://example.gov.cn/n2", "2026-03-02", "candidate", '["招聘"]', "fetched"),
                )
                conn.execute(
                    """INSERT INTO evidence_version(id, notice_id, source_url, content_sha256, parser_status)
                       VALUES ('e2', 'n2', 'https://example.gov.cn/b.xlsx', ?, 'parsed')""",
                    ("b" * 64,),
                )
                conn.execute(
                    """INSERT INTO position(id, notice_id, evidence_id, sheet_name, row_index,
                       position_code, employer, position_name, education, major_requirement)
                       VALUES ('p2', 'n2', 'e2', 'Sheet1', 3, '01', '研究院', '研究人员', '硕士研究生及以上', '计算机科学与技术')"""
                )
            profile = UserProfile(
                major="计算机科学与技术", education="本科", degree="学士",
                graduate_status="fresh", year=2026,
            )
            service = RecommendationService(WebRepository(db), LLMClient())
            result = service.recommend(profile, save_profile=True)
            self.assertEqual([item.notice_id for item in result.items], ["n1"])
            item = result.items[0]
            self.assertEqual(item.match_level, "eligible")
            self.assertEqual(len(item.positions), 1)
            self.assertEqual(item.positions[0].verdict, "eligible")
            self.assertIn("岗位级核验", item.reasons[0])
            run = db.connection.execute("SELECT result_json FROM recommendation_run").fetchone()
            payload = json.loads(run[0])
            self.assertEqual(len(payload["excluded_positions"]), 1)
            self.assertEqual(payload["excluded_positions"][0]["notice_id"], "n2")
            self.assertTrue(payload["excluded_positions"][0]["positions"][0]["reasons"])
            db.close()

    def test_position_detail_endpoint_includes_sources_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "a.db"
            db = Database(db_path)
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
                       VALUES ('n1', 's', '2026年事业单位招聘公告', '招聘', 'https://example.gov.cn/n1', '2026-03-01', 'candidate', '["招聘"]', 'fetched')"""
                )
                conn.execute(
                    """INSERT INTO evidence_version(id, notice_id, source_url, content_sha256, content_type, parser_status)
                       VALUES ('e1', 'n1', 'https://example.gov.cn/a.xlsx', ?, 'application/vnd.ms-excel', 'parsed')""",
                    ("c" * 64,),
                )
                conn.execute(
                    """INSERT INTO position(id, notice_id, evidence_id, sheet_name, row_index,
                       position_code, employer, position_name, education, major_requirement)
                       VALUES ('p1', 'n1', 'e1', 'Sheet1', 3, '01', '测试学校', '计算机教师', '本科及以上', '计算机科学与技术')"""
                )
            object_path = db.store_object("c" * 64, b"fake-xlsx-bytes")
            with db.transaction() as conn:
                conn.execute("UPDATE evidence_version SET object_path = ? WHERE id = 'e1'", (object_path,))
            db.close()

            from aurora_web.main import create_app

            app = create_app(db_path)
            routes = {route.path: route for route in app.routes}
            detail = routes["/api/v1/positions/{position_id}"].endpoint(position_id="p1")
            self.assertEqual(detail["position"]["position_code"], "01")
            # 未保存画像时按默认空画像评估，缺学历/专业应为待核实
            self.assertEqual(detail["verdict"], "needs_review")
            self.assertTrue(any(condition["label"] == "学历" for condition in detail["conditions"]))
            self.assertEqual(len(detail["sources"]), 1)
            self.assertTrue(detail["sources"][0]["is_origin"])
            self.assertTrue(detail["sources"][0]["has_file"])
            file_response = routes["/api/v1/evidence/{evidence_id}/file"].endpoint(evidence_id="e1")
            self.assertEqual(file_response.body, b"fake-xlsx-bytes")
            app.state.database.close()


if __name__ == "__main__":
    unittest.main()
