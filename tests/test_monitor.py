import hashlib
import io
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from aurora_monitor.db import Database
from aurora_monitor.fetcher import FetchResult
from aurora_monitor.monitor import Monitor


class MonitorTests(unittest.TestCase):
    def test_fetch_filter_store_and_dedupe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                '<a href="/recruit.html">2026年事业单位招聘公告</a>'
                '<a href="/noise.html">工伤送达公告</a>',
                encoding="utf-8",
            )
            db = Database(root / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], ["工伤送达"], ["面试"])
            db.add_source({
                "id": "src-test",
                "source_group": "jiangsu_city_recruitment",
                "region_code": "JS-南京",
                "publisher": "测试来源",
                "entry_url": "https://example.gov.cn/index.html",
                "allowed_domains": ["example.gov.cn"],
                "keyword_policy_id": "gov",
                "status": "enabled",
            })
            db.add_profile({"id": "profile-test", "user_id": "u1", "name": "南京事业编", "scope_types": ["public_institution"], "region_codes": ["JS-南京"]})
            db.add_profile({"id": "profile-province", "user_id": "u2", "name": "江苏事业编", "scope_types": ["public_institution"], "region_codes": ["JS"], "include_keywords": ["事业单位"]})
            db.add_profile({"id": "profile-filtered", "user_id": "u3", "name": "只看教师", "scope_types": ["public_institution"], "region_codes": ["JS-南京"], "include_keywords": ["教师"]})
            body = '<a href="/recruit.html">2026年事业单位招聘公告</a><a href="/noise.html">工伤送达公告</a>'.encode("utf-8")
            response = FetchResult("https://example.gov.cn/index.html", 200, "text/html", body, 1)
            with patch("aurora_monitor.monitor.fetch", return_value=response):
                first = Monitor(db).run_once()
                db.connection.execute("UPDATE source SET next_check_at = CURRENT_TIMESTAMP WHERE id = 'src-test'")
                db.connection.commit()
                second = Monitor(db).run_once()
            self.assertEqual(first["candidate"], 1)
            self.assertEqual(first["noise"], 1)
            self.assertEqual(first["notifications"], 2)
            self.assertEqual(second["unchanged"], 1)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM notice").fetchone()[0], 2)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM evidence_version").fetchone()[0], 1)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM notification").fetchone()[0], 2)
            self.assertEqual(len(db.list_profile_notices("profile-test")), 1)
            self.assertEqual(db.list_source_health()[0]["last_check_status"], "unchanged")
            db.close()

    def test_paginated_source_discovers_second_page(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], [], [])
            db.add_source({
                "id": "src-pages",
                "source_group": "jiangsu_city_recruitment",
                "region_code": "JS-南京",
                "publisher": "分页测试",
                "entry_url": "https://example.gov.cn/list?page=1",
                "allowed_domains": ["example.gov.cn"],
                "keyword_policy_id": "gov",
                "max_pages": 2,
            })
            page1 = '<a href="/notice/1">招聘公告一</a><a href="/list?page=2">下一页</a>'.encode()
            page2 = '<a href="/notice/2">招聘公告二</a>'.encode()

            def fake_fetch(url, allowed_domains, timeout=15, **kwargs):
                if url.endswith("page=1"):
                    return FetchResult(url, 200, "text/html", page1, 1)
                if url.endswith("page=2"):
                    return FetchResult(url, 200, "text/html", page2, 1)
                return FetchResult(url, 200, "text/html", b"detail", 1)

            with patch("aurora_monitor.monitor.fetch", side_effect=fake_fetch):
                stats = Monitor(db).run_once()
            self.assertEqual(stats["candidate"], 2)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM notice").fetchone()[0], 2)
            db.close()

    def test_detail_change_creates_versioned_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], [], [])
            db.add_source({"id": "src-change", "source_group": "jiangsu_city_recruitment", "region_code": "JS-南京", "publisher": "变更测试", "entry_url": "https://example.gov.cn/list", "keyword_policy_id": "gov"})
            db.add_profile({"id": "profile-change", "user_id": "u1", "name": "变更订阅", "scope_types": ["public_institution"], "event_types": ["new_notice", "content_change"]})
            version = {"value": 1}

            def fake_fetch(url, allowed_domains, timeout=15, **kwargs):
                if url.endswith("/list"):
                    body = f'<!-- v{version["value"]} --><a href="/notice">招聘公告</a>'.encode()
                else:
                    body = f'detail-v{version["value"]}'.encode()
                return FetchResult(url, 200, "text/html", body, 1)

            with patch("aurora_monitor.monitor.fetch", side_effect=fake_fetch):
                first = Monitor(db).run_once()
                version["value"] = 2
                db.connection.execute("UPDATE source SET next_check_at = CURRENT_TIMESTAMP WHERE id = 'src-change'")
                db.connection.commit()
                second = Monitor(db).run_once()
            self.assertEqual(first["notifications"], 1)
            self.assertEqual(second["notifications"], 1)
            events = [row[0] for row in db.connection.execute("SELECT event_type FROM notification ORDER BY id")]
            self.assertEqual(events[0], "new_notice")
            self.assertTrue(events[1].startswith("content_change:"))
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM evidence_version").fetchone()[0], 2)
            db.close()

    def test_detail_failure_does_not_notify_without_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], [], [])
            db.add_source({"id": "src-fail-detail", "source_group": "jiangsu_city_recruitment", "region_code": "JS-南京", "publisher": "详情失败测试", "entry_url": "https://example.gov.cn/list", "keyword_policy_id": "gov"})
            db.add_profile({"id": "profile-fail-detail", "user_id": "u1", "name": "详情失败订阅", "scope_types": ["public_institution"]})

            def fake_fetch(url, allowed_domains, timeout=15, **kwargs):
                if url.endswith("/list"):
                    return FetchResult(url, 200, "text/html", '<a href="/notice">招聘公告</a>'.encode(), 1)
                raise RuntimeError("detail unavailable")

            with patch("aurora_monitor.monitor.fetch", side_effect=fake_fetch):
                stats = Monitor(db).run_once()
            self.assertEqual(stats["candidate"], 1)
            self.assertEqual(stats["detail_failed"], 1)
            self.assertEqual(stats["notifications"], 0)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM notice").fetchone()[0], 1)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM notification").fetchone()[0], 0)
            db.close()

    def test_failed_detail_is_retried_on_unchanged_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], [], [])
            db.add_source({"id": "src-retry", "source_group": "jiangsu_city_recruitment", "region_code": "JS-南京", "publisher": "详情重试测试", "entry_url": "https://example.gov.cn/list", "keyword_policy_id": "gov"})
            db.add_profile({"id": "profile-retry", "user_id": "u1", "name": "重试订阅", "scope_types": ["public_institution"]})
            detail_available = {"value": False}

            def fake_fetch(url, allowed_domains, timeout=15, **kwargs):
                if url.endswith("/list"):
                    return FetchResult(url, 200, "text/html", '<a href="/notice">招聘公告</a>'.encode(), 1)
                if not detail_available["value"]:
                    raise RuntimeError("temporary detail failure")
                return FetchResult(url, 200, "text/html", b"detail", 1)

            with patch("aurora_monitor.monitor.fetch", side_effect=fake_fetch):
                first = Monitor(db).run_once()
                detail_available["value"] = True
                db.connection.execute("UPDATE notice SET next_detail_retry_at = CURRENT_TIMESTAMP")
                db.connection.execute("UPDATE source SET next_check_at = CURRENT_TIMESTAMP")
                db.connection.commit()
                second = Monitor(db).run_once()
            self.assertEqual(first["notifications"], 0)
            self.assertEqual(second["detail_fetched"], 1)
            self.assertEqual(second["notifications"], 1)
            self.assertEqual(db.connection.execute("SELECT detail_status FROM notice").fetchone()[0], "fetched")
            db.close()

    def test_source_degrades_after_failures_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], [], [])
            db.add_source({"id": "src-health", "source_group": "jiangsu_city_recruitment", "region_code": "JS-南京", "publisher": "健康测试", "entry_url": "https://example.gov.cn/list", "keyword_policy_id": "gov"})
            monitor = Monitor(db)
            for _ in range(3):
                monitor._record_check("src-health", "failed", error_message="temporary")
            row = db.connection.execute("SELECT status, consecutive_failures FROM source WHERE id='src-health'").fetchone()
            self.assertEqual((row["status"], row["consecutive_failures"]), ("degraded", 3))
            monitor._record_check("src-health", "unchanged")
            row = db.connection.execute("SELECT status, consecutive_failures FROM source WHERE id='src-health'").fetchone()
            self.assertEqual((row["status"], row["consecutive_failures"]), ("enabled", 0))
            db.close()

    def test_detail_attachment_is_saved_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], [], [])
            db.add_source({"id": "src-attachment", "source_group": "jiangsu_city_recruitment", "region_code": "JS-南京", "publisher": "附件测试", "entry_url": "https://example.gov.cn/list", "keyword_policy_id": "gov"})

            def fake_fetch(url, allowed_domains, timeout=15, **kwargs):
                if url.endswith("/list"):
                    return FetchResult(url, 200, "text/html", '<a href="/notice">招聘公告</a>'.encode(), 1)
                if url.endswith("/notice"):
                    return FetchResult(url, 200, "text/html", '<a href="/files/jobs.pdf">岗位表附件</a>'.encode(), 1)
                return FetchResult(url, 200, "application/pdf", b"%PDF-1.7 placeholder", 1)

            with patch("aurora_monitor.monitor.fetch", side_effect=fake_fetch):
                stats = Monitor(db).run_once()
            self.assertEqual(stats["detail_fetched"], 2)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM evidence_version").fetchone()[0], 2)
            urls = {row[0] for row in db.connection.execute("SELECT source_url FROM evidence_version")}
            self.assertIn("https://example.gov.cn/files/jobs.pdf", urls)
            evidence_rows = db.connection.execute(
                "SELECT id, content_sha256, body, object_path FROM evidence_version"
            ).fetchall()
            self.assertTrue(all(row["object_path"] for row in evidence_rows))
            self.assertTrue(all(row["body"] is None for row in evidence_rows))
            for row in evidence_rows:
                object_file = db.object_dir / row["object_path"]
                self.assertTrue(object_file.is_file())
                self.assertEqual(hashlib.sha256(object_file.read_bytes()).hexdigest(), row["content_sha256"])
                self.assertEqual(db.read_evidence(row["id"]), object_file.read_bytes())
            db.close()

    def test_attachment_inside_nested_subpage_is_fetched(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], [], [])
            db.add_source({"id": "src-nested", "source_group": "jiangsu_city_recruitment", "region_code": "JS-南京", "publisher": "嵌套测试", "entry_url": "https://example.gov.cn/list", "keyword_policy_id": "gov"})

            from openpyxl import Workbook

            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["单位名称", "岗位名称", "招聘人数", "学历要求"])
            sheet.append(["测试中心", "管理岗", "1", "本科及以上"])
            buffer = io.BytesIO()
            workbook.save(buffer)
            xlsx_body = buffer.getvalue()

            def fake_fetch(url, allowed_domains, timeout=15, **kwargs):
                if url.endswith("/list"):
                    return FetchResult(url, 200, "text/html", '<a href="/notice">招聘公告</a>'.encode(), 1)
                if url.endswith("/notice"):
                    return FetchResult(url, 200, "text/html", '<a href="/sub/page">岗位条件简介表</a>'.encode(), 1)
                if url.endswith("/sub/page"):
                    return FetchResult(url, 200, "text/html", '<a href="/files/jobs.xlsx">2026年招聘岗位表.xlsx</a>'.encode(), 1)
                return FetchResult(url, 200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_body, 1)

            with patch("aurora_monitor.monitor.fetch", side_effect=fake_fetch):
                stats = Monitor(db).run_once()
            self.assertEqual(stats["detail_fetched"], 2)
            urls = {row[0] for row in db.connection.execute("SELECT source_url FROM evidence_version")}
            self.assertIn("https://example.gov.cn/sub/page", urls)
            self.assertIn("https://example.gov.cn/files/jobs.xlsx", urls)
            position = db.connection.execute("SELECT employer, position_name, education FROM position").fetchone()
            self.assertEqual((position["employer"], position["position_name"], position["education"]), ("测试中心", "管理岗", "本科及以上"))
            db.close()


if __name__ == "__main__":
    unittest.main()
