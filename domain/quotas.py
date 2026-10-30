import sqlite3
import time
from contextlib import closing
from pathlib import Path


class QuotaExceededError(RuntimeError):
    """Indica que um utilizador excedeu a quota configurada."""


class UserQuotaStore:
    def __init__(
        self,
        path: str | Path = "workspace.db",
        *,
        max_operations: int = 100,
        window_seconds: int = 86400,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_operations = max_operations
        self.window_seconds = window_seconds
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_quota_events (
                    user_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.commit()

    def consume(self, user_id: str, operation: str) -> None:
        if not user_id:
            raise ValueError("É necessário identificar o utilizador para aplicar quotas.")
        cutoff = time.time() - self.window_seconds
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM user_quota_events WHERE created_at < ?",
                    (cutoff,),
                )
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM user_quota_events
                    WHERE user_id = ? AND created_at >= ?
                    """,
                    (user_id, cutoff),
                ).fetchone()[0]
                if count >= self.max_operations:
                    raise QuotaExceededError(
                        f"Quota diária excedida para o utilizador ({self.max_operations} operações)."
                    )
                connection.execute(
                    "INSERT INTO user_quota_events(user_id, operation, created_at) VALUES (?, ?, ?)",
                    (user_id, operation, time.time()),
                )
