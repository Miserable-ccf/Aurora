import unittest

from aurora_monitor.major_catalog import load_major_catalog


class MajorCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_major_catalog()

    def test_catalog_is_bundled(self):
        self.assertIsNotNone(self.catalog)
        self.assertIn("计算机类", self.catalog.category_names())
        self.assertIn("法律类", self.catalog.category_names())

    def test_exact_major_matches_category(self):
        belongs, basis = self.catalog.match("计算机类", "计算机科学与技术", "本科")
        self.assertTrue(belongs)
        self.assertIn("专业参考目录", basis)

    def test_cross_reference_expansion(self):
        belongs, _ = self.catalog.match("计算机类", "软件工程", "本科")
        self.assertTrue(belongs)

    def test_major_outside_category(self):
        belongs, basis = self.catalog.match("计算机类", "会计学", "本科")
        self.assertFalse(belongs)
        self.assertIn("未在", basis)

    def test_level_specific_listing(self):
        self.assertEqual(self.catalog.find_categories("网络与信息安全", "本科"), [])
        self.assertIn("计算机（网络管理）类", self.catalog.find_categories("网络与信息安全", "研究生"))

    def test_parenthesized_variant_matches(self):
        belongs, _ = self.catalog.match("法律类", "法学（涉外法律）", "本科")
        self.assertTrue(belongs)


if __name__ == "__main__":
    unittest.main()
