import unittest

from domain.quality import build_review_result


class QualityGateTests(unittest.TestCase):
    def test_requires_score_and_no_blocking_issues(self):
        report = build_review_result(approved=True, score=90)
        self.assertTrue(report.approved)

        report = build_review_result(
            approved=True,
            score=90,
            blocking_issues=["Ficheiro inexistente"],
        )
        self.assertFalse(report.approved)

    def test_rejects_score_below_threshold(self):
        report = build_review_result(approved=True, score=84)
        self.assertFalse(report.approved)


if __name__ == "__main__":
    unittest.main()
