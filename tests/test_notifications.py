import tempfile
import unittest
from pathlib import Path

from aurora_monitor.db import Database
from aurora_monitor.notifications import NotificationDispatcher


class NotificationTests(unittest.TestCase):
    def test_pending_notification_is_dispatched_once(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "aurora.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("gov", 1, ["招聘"], [], [])
            db.add_source({"id": "s1", "source_group": "jiangsu_city_recruitment", "region_code": "JS-南京", "publisher": "测试", "entry_url": "https://example.gov.cn", "keyword_policy_id": "gov"})
            db.add_profile({"id": "p1", "user_id": "u1", "name": "测试配置", "scope_types": ["public_institution"], "channel": "test"})
            with db.transaction() as conn:
                conn.execute("INSERT INTO notice(id, source_id, title, normalized_title, url, decision) VALUES ('n1', 's1', '招聘公告', '招聘公告', 'https://example.gov.cn/n1', 'candidate')")
                conn.execute("INSERT INTO notification(profile_id, notice_id, event_type) VALUES ('p1', 'n1', 'new_notice')")
            sent_payloads = []
            dispatcher = NotificationDispatcher(db, {"test": sent_payloads.append})
            self.assertEqual(dispatcher.dispatch().sent, 1)
            self.assertEqual(dispatcher.dispatch().sent, 0)
            row = db.connection.execute("SELECT status, attempts FROM notification").fetchone()
            self.assertEqual((row["status"], row["attempts"]), ("sent", 1))
            self.assertEqual(sent_payloads[0]["title"], "招聘公告")
            db.close()


if __name__ == "__main__":
    unittest.main()
