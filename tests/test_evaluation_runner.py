import unittest

from evaluations.runner import evaluate_training_output, load_all_cases


class EvaluationRunnerTests(unittest.TestCase):
    def test_loads_all_evaluation_files(self):
        cases = load_all_cases()
        names = {case["name"] for case in cases}
        self.assertIn("marketing-tiktok", names)
        self.assertIn("pesquisa-factual", names)

    def test_passes_complete_training_output(self):
        case = {
            "name": "case",
            "required": ["plano", "objetivos"],
            "minimum_quality_score": 85,
        }
        result = evaluate_training_output("Plano com objetivos.", case)
        self.assertTrue(result["passed"])
        self.assertEqual(result["score"], 100)

    def test_reports_missing_requirements(self):
        result = evaluate_training_output(
            "Apenas plano.",
            {"name": "case", "required": ["plano", "avaliação"], "minimum_quality_score": 85},
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing"], ["avaliação"])


if __name__ == "__main__":
    unittest.main()
