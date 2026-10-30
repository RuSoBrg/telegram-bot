import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domain.models import WorkflowRun
from workers import workflow_worker


class WorkerTests(unittest.TestCase):
    def test_worker_executes_and_persists_result(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "workflow.db"
            fake_graph = [
                {"iteration": 2, "plan": [{"order": 1, "status": "done"}]},
            ]
            payload = {
                "request_id": "request-1",
                "thread_id": "thread-1",
                "user_id": "user-1",
                "text": "pedido",
            }
            with patch.dict(os.environ, {"WORKFLOW_DB_PATH": str(run_path)}):
                with patch.object(workflow_worker, "grafo") as graph:
                    graph.stream.return_value = iter(fake_graph)
                    result = workflow_worker.execute_task(payload)
                self.assertEqual(result["status"], "approved")
                run = workflow_worker.WorkflowStore(run_path).get("request-1")
                self.assertIsInstance(run, WorkflowRun)
                self.assertEqual(run.iteration, 2)

    def test_worker_payload_is_json_compatible(self):
        payload = {"request_id": "1", "text": "pedido"}
        self.assertEqual(json.loads(json.dumps(payload)), payload)
