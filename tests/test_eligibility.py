import unittest
from types import SimpleNamespace

from aurora_monitor.eligibility import evaluate_position_row


def make_profile(**overrides):
    defaults = dict(
        education="本科",
        degree="学士",
        major="法学",
        graduate_status="fresh",
        political_status="中共党员",
        grassroots_years=3,
        certificates=["教师资格证"],
        year=2026,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_position(**overrides):
    defaults = dict(
        position_code="01",
        employer="测试单位",
        position_name="综合管理",
        education="本科及以上",
        degree="取得相应学位",
        major_requirement="法学",
        fresh_graduate_requirement="限2026年应届高校毕业生",
        political_requirement="中共（预备）党员",
        grassroots_requirement="具有两年及以上基层工作经历",
        certificate_requirement="教师资格证",
    )
    defaults.update(overrides)
    return defaults


class EligibilityTests(unittest.TestCase):
    def test_all_hard_conditions_pass(self):
        evaluation = evaluate_position_row(make_position(), make_profile())
        self.assertEqual(evaluation.verdict, "eligible")
        self.assertEqual(evaluation.questions, [])
        self.assertTrue(all(check.verdict == "符合" for check in evaluation.checks))

    def test_low_education_fails(self):
        evaluation = evaluate_position_row(make_position(education="硕士研究生及以上"), make_profile())
        self.assertEqual(evaluation.verdict, "not_eligible")
        failed = [check for check in evaluation.checks if check.verdict == "不符合"]
        self.assertEqual(failed[0].field, "education")

    def test_exact_level_higher_user_needs_review(self):
        evaluation = evaluate_position_row(make_position(education="本科"), make_profile(education="硕士研究生"))
        education_check = next(check for check in evaluation.checks if check.field == "education")
        self.assertEqual(education_check.verdict, "待核实")
        self.assertEqual(evaluation.verdict, "needs_review")

    def test_category_major_resolved_by_official_catalog(self):
        evaluation = evaluate_position_row(
            make_position(major_requirement="计算机类"), make_profile(major="计算机科学与技术", education="本科")
        )
        major_check = next(check for check in evaluation.checks if check.field == "major_requirement")
        self.assertEqual(major_check.verdict, "符合")
        self.assertIn("专业参考目录", major_check.reason)

    def test_unknown_category_still_needs_review(self):
        evaluation = evaluate_position_row(
            make_position(major_requirement="飞行器类"), make_profile(major="飞行器设计与工程")
        )
        self.assertEqual(evaluation.verdict, "needs_review")
        self.assertTrue(any("专业目录" in question for question in evaluation.questions))

    def test_category_major_without_stem_fails(self):
        evaluation = evaluate_position_row(
            make_position(major_requirement="法律类"), make_profile(major="会计学", education="本科")
        )
        major_check = next(check for check in evaluation.checks if check.field == "major_requirement")
        self.assertEqual(major_check.verdict, "不符合")
        self.assertIn("专业参考目录", major_check.reason)
        self.assertEqual(evaluation.verdict, "not_eligible")

    def test_alternative_majors_pass_when_one_matches(self):
        evaluation = evaluate_position_row(
            make_position(major_requirement="经济类；计算机类；法学"), make_profile(major="法学")
        )
        major_check = next(check for check in evaluation.checks if check.field == "major_requirement")
        self.assertEqual(major_check.verdict, "符合")

    def test_fresh_graduate_requirement(self):
        fresh_position = make_position()
        self.assertEqual(evaluate_position_row(fresh_position, make_profile(graduate_status="non_fresh")).verdict, "not_eligible")
        self.assertEqual(evaluate_position_row(fresh_position, make_profile(graduate_status="unknown")).verdict, "needs_review")

    def test_graduation_year_wording(self):
        position = make_position(fresh_graduate_requirement="2026年毕业生")
        self.assertEqual(evaluate_position_row(position, make_profile(graduate_status="fresh")).verdict, "eligible")
        self.assertEqual(evaluate_position_row(position, make_profile(graduate_status="non_fresh")).verdict, "not_eligible")
        mismatched = make_position(fresh_graduate_requirement="2025年毕业生")
        evaluation = evaluate_position_row(mismatched, make_profile(graduate_status="fresh"))
        fresh_check = next(check for check in evaluation.checks if check.field == "fresh_graduate_requirement")
        self.assertEqual(fresh_check.verdict, "不符合")

    def test_missing_certificate_fails_when_profile_has_certificates(self):
        evaluation = evaluate_position_row(
            make_position(certificate_requirement="法律职业资格证"),
            make_profile(certificates=["教师资格证"]),
        )
        certificate_check = next(check for check in evaluation.checks if check.field == "certificate_requirement")
        self.assertEqual(certificate_check.verdict, "不符合")

    def test_certificate_without_profile_entry_needs_review(self):
        evaluation = evaluate_position_row(make_position(), make_profile(certificates=[]))
        self.assertEqual(evaluation.verdict, "needs_review")

    def test_missing_profile_fields_never_fail(self):
        evaluation = evaluate_position_row(make_position(), make_profile(education="", degree="", major="", political_status="", grassroots_years=None))
        self.assertEqual(evaluation.verdict, "needs_review")
        self.assertFalse(any(check.verdict == "不符合" for check in evaluation.checks))

    def test_unknown_requirements_pass_through(self):
        evaluation = evaluate_position_row(
            make_position(
                degree="unknown", political_requirement="unknown", grassroots_requirement="unknown",
                certificate_requirement="unknown", fresh_graduate_requirement="unknown",
            ),
            make_profile(),
        )
        self.assertEqual(evaluation.verdict, "eligible")
        self.assertTrue(any("职位表未列出该项要求" in check.reason for check in evaluation.checks))

    def test_age_and_gender_and_household_are_review_questions(self):
        evaluation = evaluate_position_row(
            make_position(age_requirement="35周岁以下", gender_requirement="男性", household_requirement="限南京户籍"),
            make_profile(),
        )
        self.assertEqual(evaluation.verdict, "needs_review")
        self.assertEqual(len(evaluation.questions), 3)

    def test_unrestricted_terms_pass(self):
        evaluation = evaluate_position_row(
            make_position(major_requirement="不限", fresh_graduate_requirement="不限", political_requirement="不限"),
            make_profile(graduate_status="non_fresh", political_status="群众"),
        )
        self.assertEqual(evaluation.verdict, "eligible")

    def test_grassroots_years_compare(self):
        position = make_position(grassroots_requirement="具有五年及以上基层工作经历")
        self.assertEqual(evaluate_position_row(position, make_profile(grassroots_years=3)).verdict, "not_eligible")
        self.assertEqual(evaluate_position_row(position, make_profile(grassroots_years=6)).verdict, "eligible")

    def test_work_experience_without_grassroots_is_review(self):
        evaluation = evaluate_position_row(
            make_position(grassroots_requirement="unknown", other_requirements="具有2年及以上工作经验"),
            make_profile(),
        )
        self.assertEqual(evaluation.verdict, "needs_review")
        self.assertTrue(any("工作" in question for question in evaluation.questions))


if __name__ == "__main__":
    unittest.main()
