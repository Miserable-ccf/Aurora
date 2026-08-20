import unittest

from aurora_monitor.filtering import decide


class FilteringTests(unittest.TestCase):
    def test_noise_is_rejected(self):
        result = decide("工伤送达公告", ["招聘"], ["工伤送达"], ["面试"])
        self.assertEqual(result.decision, "noise")

    def test_recruitment_title_is_candidate(self):
        result = decide("2026年事业单位公开招聘公告", ["招聘"], ["成绩公示"], ["面试"])
        self.assertEqual(result.decision, "candidate")

    def test_conflicting_title_needs_review(self):
        result = decide("事业单位招聘面试成绩公示", ["招聘"], ["成绩公示"], ["面试"])
        self.assertEqual(result.decision, "needs_review")


class ProcessTermTests(unittest.TestCase):
    def test_process_notice_is_noise_even_with_recruitment_terms(self):
        result = decide("2026年公开招聘教师考试总成绩及进入体检名单公告", ["招聘"], [], [], ["总成绩", "名单"])
        self.assertEqual(result.decision, "noise")

    def test_supplement_announcement_is_filtered(self):
        result = decide("2026年公开招聘工作人员补充公告", ["招聘"], [], [], ["补充公告"])
        self.assertEqual(result.decision, "noise")

    def test_recruitment_announcement_stays_candidate(self):
        result = decide("2026年公开招聘教师公告", ["招聘"], [], [], ["总成绩", "补充公告"])
        self.assertEqual(result.decision, "candidate")

    def test_backward_compatible_without_process_terms(self):
        result = decide("事业单位招聘面试成绩公示", ["招聘"], ["成绩公示"], ["面试"])
        self.assertEqual(result.decision, "needs_review")


if __name__ == "__main__":
    unittest.main()
