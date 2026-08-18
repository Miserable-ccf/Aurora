import json
import tempfile
import unittest
from pathlib import Path

from aurora_monitor.db import Database


class ImportTests(unittest.TestCase):
    def test_array_policy_and_source_files_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policies.json"
            source_path = root / "sources.json"
            policy_path.write_text(json.dumps([{"id": "p", "version": 1, "include_any": ["招聘"], "exclude_any": [], "workflow_terms": []}], ensure_ascii=False), encoding="utf-8")
            source_path.write_text(json.dumps([{"id": "s", "source_group": "jiangsu_province_hrss", "region_code": "JS", "publisher": "test", "entry_url": "https://example.gov.cn", "allowed_domains": ["example.gov.cn"], "keyword_policy_id": "p"}], ensure_ascii=False), encoding="utf-8")
            db = Database(root / "a.db")
            db.init_schema()
            db.seed_regions()
            self.assertEqual(db.add_policy_json(policy_path), 1)
            self.assertEqual(db.import_sources_json(source_path), 1)
            self.assertEqual(db.import_sources_json(source_path), 1)
            db.close()

    def test_yaml_import_rejects_duplicate_urls_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "sources.yaml"
            source_path.write_text(
                """policy:
  keyword_policy: p
sources:
  - id: a
    source_group: jiangsu_province_hrss
    region: 江苏
    publisher: a
    entry_url: https://example.gov.cn/list
  - id: b
    source_group: jiangsu_province_recruitment
    region: 江苏
    publisher: b
    entry_url: https://example.gov.cn/list
""",
                encoding="utf-8",
            )
            db = Database(root / "a.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("p", 1, ["招聘"], [], [])
            with self.assertRaisesRegex(ValueError, "duplicate entry_url"):
                db.import_sources_yaml(source_path)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM source").fetchone()[0], 0)
            db.close()

    def test_yaml_import_rejects_placeholder_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "sources.yaml"
            source_path.write_text(
                """policy:
  keyword_policy: p
sources:
  - id: a
    source_group: jiangsu_province_hrss
    region: 江苏
    publisher: a
    entry_url: https://<待核验>/
""",
                encoding="utf-8",
            )
            db = Database(root / "a.db")
            db.init_schema()
            db.seed_regions()
            db.upsert_policy("p", 1, ["招聘"], [], [])
            with self.assertRaisesRegex(ValueError, "placeholder URL"):
                db.import_sources_yaml(source_path)
            db.close()


if __name__ == "__main__":
    unittest.main()
