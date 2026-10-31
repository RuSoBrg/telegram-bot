import time
import sqlite3
from pathlib import Path
from contextlib import closing

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
        self._database_url = str(_database_url) if _database_url else None
        self._sqlite = bool(self._database_url and not self._database_url.startswith(("postgresql://", "postgres://")))
        if self._sqlite:
            Path(self._database_url).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._database_url)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS user_quota_events (
                            user_id TEXT NOT NULL,
                            operation TEXT NOT NULL,
                            created_at REAL NOT NULL
                        )
                        """
                    )
            return
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
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
                with connection:
                    connection.execute(
                        "DELETE FROM user_quota_events WHERE created_at < ?",
                        (cutoff,),
                    )
                    count = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM user_quota_events
                        WHERE user_id = ? AND created_at >= ?
                        """,
                        (user_id, cutoff),
                    ).fetchone()[0]
                    if count >= self.max_operations:
                        raise QuotaExceededError(
                            f"Quota diária excedida para o utilizador ({self.max_operations} operações)."
                        )
                    connection.execute(
                        """
                        INSERT INTO user_quota_events(user_id, operation, created_at)
                        VALUES (?, ?, ?)
                        """,
                        (user_id, operation, time.time()),
                    )
            return
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
