import sqlite3
from contextlib import closing
from pathlib import Path

from domain.models import WorkflowRun


class WorkflowStore:
    """Persistência configurável: SQLite local ou PostgreSQL via URL."""

    def __init__(self, path: str | Path = "workspace.db"):
        if str(path).startswith(("postgresql://", "postgres://")):
            self._backend = PostgresWorkflowStore(str(path))
            return
        self._backend = None
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
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

    def save(self, run: WorkflowRun) -> None:
        if self._backend:
            self._backend.save(run)
            return
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
        if self._backend:
            return self._backend.get(request_id)
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT payload FROM workflow_runs WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return WorkflowRun.model_validate_json(row[0]) if row else None

    def list_recent(self, limit: int = 20) -> list[WorkflowRun]:
        if self._backend:
            return self._backend.list_recent(limit)
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT payload FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [WorkflowRun.model_validate_json(row[0]) for row in rows]


class PostgresWorkflowStore:
    """Backend PostgreSQL opcional, ativado por WORKFLOW_DB_PATH como URL."""

    def __init__(self, url: str):
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL requer a dependência opcional 'psycopg[binary]'."
            ) from error
        self._psycopg = psycopg
        self.url = url
        with self._connect() as connection:
            with connection.cursor() as cursor:
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
            connection.commit()

    def _connect(self):
        return self._psycopg.connect(self.url)

    def save(self, run: WorkflowRun) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
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
            connection.commit()

    def get(self, request_id: str) -> WorkflowRun | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT payload::text FROM workflow_runs WHERE request_id = %s",
                    (request_id,),
                )
                row = cursor.fetchone()
        return WorkflowRun.model_validate_json(row[0]) if row else None

    def list_recent(self, limit: int = 20) -> list[WorkflowRun]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT payload::text FROM workflow_runs ORDER BY updated_at DESC LIMIT %s",
                    (limit,),
                )
                rows = cursor.fetchall()
        return [WorkflowRun.model_validate_json(row[0]) for row in rows]
