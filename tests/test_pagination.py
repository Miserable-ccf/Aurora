import unittest

from aurora_monitor.adapters import CandidateLink, is_next_page


class PaginationTests(unittest.TestCase):
    def test_only_next_labels_are_followed(self):
        self.assertTrue(is_next_page(CandidateLink("下一页", "https://example.gov.cn/list?page=2")))
        self.assertTrue(is_next_page(CandidateLink("Next", "https://example.gov.cn/list?page=2")))
        self.assertFalse(is_next_page(CandidateLink("招聘公告", "https://example.gov.cn/list?page=2")))


if __name__ == "__main__":
    unittest.main()
