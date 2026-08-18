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


if __name__ == "__main__":
    unittest.main()
