from domain.database import connect
from domain.models import WorkflowRun


class WorkflowStore:
    """Persiste execuções do workflow no PostgreSQL local."""

    def __init__(self, _database_url: str | None = None):
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
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload::text FROM workflow_runs WHERE request_id = %s",
                (request_id,),
            )
            row = cursor.fetchone()
        return WorkflowRun.model_validate_json(row[0]) if row else None

    def list_recent(self, limit: int = 20) -> list[WorkflowRun]:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload::text FROM workflow_runs ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            )
            rows = cursor.fetchall()
        return [WorkflowRun.model_validate_json(row[0]) for row in rows]
