import json
import sqlite3
from contextlib import closing
from pathlib import Path

from domain.models import WorkflowRun


class WorkflowStore:
    """Persistência SQLite simples para retomar e auditar workflows."""

    def __init__(self, path: str | Path = "workspace.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    request_id TEXT PRIMARY KEY,
                    workflow TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save(self, run: WorkflowRun) -> None:
        payload = run.model_dump_json()
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                """
                INSERT INTO workflow_runs(request_id, workflow, user_id, status, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    status=excluded.status,
                    payload=excluded.payload,
                    updated_at=CURRENT_TIMESTAMP
                """,
                    (run.request_id, run.workflow, run.user_id, run.status.value, payload),
                )

    def get(self, request_id: str) -> WorkflowRun | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT payload FROM workflow_runs WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return WorkflowRun.model_validate_json(row[0]) if row else None

    def list_recent(self, limit: int = 20) -> list[WorkflowRun]:
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT payload FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [WorkflowRun.model_validate_json(row[0]) for row in rows]
