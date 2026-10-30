import os
import sqlite3
import time
from contextlib import contextmanager, closing
from pathlib import Path


class LockTimeoutError(TimeoutError):
    """O lock não ficou disponível dentro do prazo configurado."""


class WorkflowLockStore:
    """Locks distribuídos com Redis e fallback SQLite para desenvolvimento local."""

    def __init__(
        self,
        path: str | Path = "workspace.db",
        *,
        redis_url: str = "",
        timeout_seconds: int = 30,
    ):
        self.timeout_seconds = timeout_seconds
        self._redis = None
        if redis_url:
            import redis

            self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_locks (
                    lock_key TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            connection.commit()

    @contextmanager
    def acquire(self, lock_key: str, owner: str):
        if not lock_key or not owner:
            raise ValueError("lock_key e owner são obrigatórios.")
        if self._redis is not None:
            token = f"{owner}:{time.time_ns()}"
            acquired = self._redis.set(
                f"ai-workspace:lock:{lock_key}",
                token,
                nx=True,
                ex=self.timeout_seconds,
            )
            if not acquired:
                raise LockTimeoutError(f"Lock ocupado: {lock_key}")
            try:
                yield
            finally:
                if self._redis.get(f"ai-workspace:lock:{lock_key}") == token:
                    self._redis.delete(f"ai-workspace:lock:{lock_key}")
            return

        now = time.time()
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM workflow_locks WHERE expires_at <= ?",
                    (now,),
                )
                try:
                    connection.execute(
                        "INSERT INTO workflow_locks(lock_key, owner, expires_at) VALUES (?, ?, ?)",
                        (lock_key, owner, now + self.timeout_seconds),
                    )
                except sqlite3.IntegrityError as error:
                    raise LockTimeoutError(f"Lock ocupado: {lock_key}") from error
        try:
            yield
        finally:
            with closing(sqlite3.connect(self.path)) as connection:
                with connection:
                    connection.execute(
                        "DELETE FROM workflow_locks WHERE lock_key = ? AND owner = ?",
                        (lock_key, owner),
                    )


class TaskQueue:
    """Fila Redis para tarefas temporárias; falha explicitamente sem Redis."""

    def __init__(self, redis_url: str | None = None):
        url = redis_url or os.getenv("REDIS_URL", "")
        self._redis = None
        if url:
            import redis

            self._redis = redis.Redis.from_url(url, decode_responses=True)

    def enqueue(self, queue: str, payload: str) -> None:
        if self._redis is None:
            raise RuntimeError("REDIS_URL não está configurada para usar a fila.")
        self._redis.rpush(f"ai-workspace:queue:{queue}", payload)

    def dequeue(self, queue: str, timeout_seconds: int = 1) -> str | None:
        if self._redis is None:
            raise RuntimeError("REDIS_URL não está configurada para usar a fila.")
        result = self._redis.blpop(
            f"ai-workspace:queue:{queue}",
            timeout=timeout_seconds,
        )
        return result[1] if result else None
