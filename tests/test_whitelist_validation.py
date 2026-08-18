import csv
import tempfile
import unittest
from pathlib import Path

from aurora_monitor.db import Database


class WhitelistValidationTests(unittest.TestCase):
    def test_validation_reports_bad_rows_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "institutions.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["institution_id", "institution_name", "city", "ownership", "school_level", "official_domain", "official_site_url"])
                writer.writeheader()
                writer.writerow({"institution_id": "ok", "institution_name": "学校", "city": "南京", "ownership": "public", "school_level": "vocational", "official_domain": "ok.edu.cn", "official_site_url": "https://ok.edu.cn"})
                writer.writerow({"institution_id": "bad", "institution_name": "学校", "city": "北京", "ownership": "private", "school_level": "undergraduate", "official_domain": "bad.edu.cn", "official_site_url": "https://bad.edu.cn"})
            db = Database(Path(directory) / "a.db")
            db.init_schema()
            db.seed_regions()
            result = db.validate_institutions_csv(path)
            self.assertEqual((result["valid"], result["invalid"]), (1, 1))
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM institution").fetchone()[0], 0)
            db.close()


if __name__ == "__main__":
    unittest.main()
