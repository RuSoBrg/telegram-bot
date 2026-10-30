import json
import tempfile
import unittest
from pathlib import Path

from domain.models import TrainingActivity, TrainingPackage, WorkflowRun, WorkflowStatus
from domain.persistence import WorkflowStore
from tools.validation_tools import validar_artefactos


class DomainTests(unittest.TestCase):
    def test_training_package_requires_objectives_and_activity(self):
        package = TrainingPackage(
            title="Python",
            audience="Iniciantes",
            duration_minutes=120,
            learning_objectives=["Definir uma função"],
            session_plan=[
                TrainingActivity(
                    title="Prática",
                    duration_minutes=30,
                    objective="Definir uma função",
                    instructions="Resolver exercício",
                )
            ],
            assessment=["Exercício prático"],
        )
        self.assertEqual(package.session_plan[0].objective, "Definir uma função")

    def test_workflow_store_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(Path(directory) / "workflow.db")
            run = WorkflowRun(
                request_id="request-1",
                workflow="training",
                user_id="user-1",
                status=WorkflowStatus.PRODUCING,
            )
            store.save(run)
            self.assertEqual(store.get("request-1").status, WorkflowStatus.PRODUCING)

    def test_artifact_validation_rejects_outside_paths(self):
        result = json.loads(validar_artefactos.invoke({"paths": [str(Path.cwd() / "graph.py")]}))
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
