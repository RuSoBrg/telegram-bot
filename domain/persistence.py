import sqlite3
from pathlib import Path
from contextlib import closing

from domain.database import connect
from domain.models import WorkflowRun


class WorkflowStore:
    """Persiste execuções do workflow no PostgreSQL local."""

    def __init__(self, _database_url: str | None = None):
        self._database_url = str(_database_url) if _database_url else None
        self._sqlite = bool(self._database_url and not self._database_url.startswith(("postgresql://", "postgres://")))
        if self._sqlite:
            Path(self._database_url).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._database_url)) as connection:
                with connection:
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
            return
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    request_id TEXT PRIMARY KEY,
                    workflow TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save(self, run: WorkflowRun) -> None:
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
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
                        (
                            run.request_id,
                            run.workflow,
                            run.user_id,
                            run.status.value,
                            run.model_dump_json(),
                        ),
                    )
            return
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO workflow_runs(request_id, workflow, user_id, status, payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT(request_id) DO UPDATE SET
                    status=excluded.status,
                    payload=excluded.payload,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    run.request_id,
                    run.workflow,
                    run.user_id,
                    run.status.value,
                    run.model_dump_json(),
                ),
            )

    def get(self, request_id: str) -> WorkflowRun | None:
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
                row = connection.execute(
                    "SELECT payload FROM workflow_runs WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
            return WorkflowRun.model_validate_json(row[0]) if row else None
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload::text FROM workflow_runs WHERE request_id = %s",
                (request_id,),
            )
            row = cursor.fetchone()
        return WorkflowRun.model_validate_json(row[0]) if row else None

    def list_recent(self, limit: int = 20) -> list[WorkflowRun]:
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
                rows = connection.execute(
                    "SELECT payload FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [WorkflowRun.model_validate_json(row[0]) for row in rows]
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload::text FROM workflow_runs ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            )
            rows = cursor.fetchall()
        return [WorkflowRun.model_validate_json(row[0]) for row in rows]
