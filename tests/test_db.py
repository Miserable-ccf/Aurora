import csv
import tempfile
import unittest
from pathlib import Path

from aurora_monitor.db import Database


class DatabaseTests(unittest.TestCase):
    def test_schema_and_whitelist_import(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            db.init_schema()
            db.seed_regions()
            csv_path = Path(directory) / "institutions.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["institution_id", "institution_name", "city", "ownership", "school_level", "official_domain", "official_site_url"])
                writer.writeheader()
                writer.writerow({"institution_id": "nj-1", "institution_name": "测试职业学院", "city": "南京", "ownership": "public", "school_level": "vocational", "official_domain": "example.edu.cn", "official_site_url": "https://example.edu.cn"})
            self.assertEqual(db.import_institutions_csv(csv_path, "batch-1")["inserted"], 1)
            row = db.connection.execute("SELECT status, region_code FROM institution WHERE id = 'nj-1'").fetchone()
            self.assertEqual((row["status"], row["region_code"]), ("verified", "JS-南京"))
            db.close()


if __name__ == "__main__":
    unittest.main()
