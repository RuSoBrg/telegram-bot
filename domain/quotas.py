import time

from domain.database import connect


class QuotaExceededError(RuntimeError):
    """Indica que um utilizador excedeu a quota configurada."""


class UserQuotaStore:
    def __init__(
        self,
        _database_url: str | None = None,
        *,
        max_operations: int = 100,
        window_seconds: int = 86400,
    ):
        self.max_operations = max_operations
        self.window_seconds = window_seconds
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_quota_events (
                    user_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                )
                """
            )

    def consume(self, user_id: str, operation: str) -> None:
        if not user_id:
            raise ValueError("É necessário identificar o utilizador para aplicar quotas.")
        cutoff = time.time() - self.window_seconds
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_quota_events WHERE created_at < %s",
                (cutoff,),
            )
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM user_quota_events
                WHERE user_id = %s AND created_at >= %s
                """,
                (user_id, cutoff),
            )
            if cursor.fetchone()[0] >= self.max_operations:
                raise QuotaExceededError(
                    f"Quota diária excedida para o utilizador ({self.max_operations} operações)."
                )
            cursor.execute(
                """
                INSERT INTO user_quota_events(user_id, operation, created_at)
                VALUES (%s, %s, %s)
                """,
                (user_id, operation, time.time()),
            )
