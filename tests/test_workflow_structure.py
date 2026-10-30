import unittest
from unittest.mock import patch

from domain.state import WorkflowState
from workflows.training_workflow import analyze_requirements, require_training_package
from langchain_core.messages import ToolMessage
from domain.models import RequirementsContext


class WorkflowStructureTests(unittest.TestCase):
    def test_training_requirements_are_extracted_from_user_message(self):
        state: WorkflowState = {
            "messages": [{"type": "human", "content": "Criar sessão de Python"}]
        }
        with patch(
            "workflows.training_workflow.requirements_model"
        ) as requirements_model:
            requirements_model.return_value.invoke.return_value = RequirementsContext(
                objective="Criar sessão de Python"
            )
            result = analyze_requirements(state)
        self.assertEqual(result.update["objective"], "Criar sessão de Python")
        self.assertEqual(result.update["status"], "researching")

    def test_training_package_gate_requests_contract(self):
        result = require_training_package({"messages": []})
        self.assertEqual(result["status"], "reviewing")
        self.assertIn("TrainingPackage", result["quality_feedback"])

    def test_valid_package_advances_planner_to_quality(self):
        result = require_training_package(
            {
                "plan": [
                    {"order": 1, "status": "in_progress"},
                    {"order": 9, "status": "pending"},
                ],
                "messages": [
                    ToolMessage(
                        content='{"valid": true}',
                        tool_call_id="validation",
                    )
                ],
            }
        )
        self.assertEqual(result["current_plan_step"], 9)
        self.assertEqual(result["plan"][0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
