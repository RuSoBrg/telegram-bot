"""Persistência única para workflow runs, quotas, artefactos e checkpoints."""

from __future__ import annotations

import hashlib
import asyncio
import sqlite3
import time
import logging
from contextlib import AbstractAsyncContextManager, AbstractContextManager, closing
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)
FALLBACK_SQLITE_PATH = "data/local_fallback.db"

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.settings import get_settings
from domain.models import ArtifactRecord, WorkflowRun


def database_url() -> str:
    url = (get_settings().database_url or get_settings().workflow_db_path).strip()
    if url.startswith(("postgresql+asyncpg://", "postgres+asyncpg://")):
        url = url.replace("+asyncpg", "", 1)
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL deve conter uma connection string PostgreSQL.")
    return url


def connect():
    return psycopg.connect(database_url())


def _sqlite_path(value: str | Path | None) -> str | None:
    if value is None:
        return None
    value = str(value)
    return value if not value.startswith(("postgresql://", "postgres://")) else None


async def init_db() -> None:
    """Inicializa as tabelas próprias da aplicação sem bloquear o event loop."""
    await asyncio.to_thread(_init_db_sync)


def _init_db_sync() -> None:
    configured = get_settings().database_url or get_settings().workflow_db_path
    sqlite_path = _sqlite_path(configured)
    if sqlite_path:
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(sqlite_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    request_id TEXT PRIMARY KEY, workflow TEXT NOT NULL,
                    user_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS user_quota_events (
                    user_id TEXT NOT NULL, operation TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL, content BLOB NOT NULL,
                    sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
        return
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                request_id TEXT PRIMARY KEY, workflow TEXT NOT NULL,
                user_id TEXT NOT NULL, status TEXT NOT NULL, payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_quota_events (
                user_id TEXT NOT NULL, operation TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                mime_type TEXT NOT NULL, content BYTEA NOT NULL,
                sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


class WorkflowStore:
    def __init__(self, database: str | Path | None = None):
        self._database = str(database) if database else None
        self._sqlite = _sqlite_path(database) is not None
        if self._sqlite:
            _init_sqlite(self._database)
        else:
            try:
                _ensure_table("workflow_runs", """
                    request_id TEXT PRIMARY KEY, workflow TEXT NOT NULL, user_id TEXT NOT NULL,
                    status TEXT NOT NULL, payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                """)
            except Exception as error:
                logger.warning("Postgres indisponível para WorkflowStore (%s); a usar fallback SQLite.", error)
                self._database = FALLBACK_SQLITE_PATH
                self._sqlite = True
                _init_sqlite(self._database)

    def save(self, run: WorkflowRun) -> None:
        if self._sqlite:
            with closing(sqlite3.connect(self._database)) as connection, connection:
                connection.execute(
                    """INSERT INTO workflow_runs(request_id,workflow,user_id,status,payload)
                    VALUES (?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET
                    status=excluded.status,payload=excluded.payload,updated_at=CURRENT_TIMESTAMP""",
                    (run.request_id, run.workflow, run.user_id, run.status.value, run.model_dump_json()),
                )
            return
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO workflow_runs(request_id,workflow,user_id,status,payload)
                VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT(request_id) DO UPDATE SET
                status=excluded.status,payload=excluded.payload,updated_at=CURRENT_TIMESTAMP""",
                (run.request_id, run.workflow, run.user_id, run.status.value, run.model_dump_json()),
            )

    def get(self, request_id: str) -> WorkflowRun | None:
        rows = self._query("SELECT payload FROM workflow_runs WHERE request_id = ?", (request_id,))
        return WorkflowRun.model_validate_json(rows[0][0]) if rows else None

    def list_recent(self, limit: int = 20) -> list[WorkflowRun]:
        rows = self._query("SELECT payload FROM workflow_runs ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [WorkflowRun.model_validate_json(row[0]) for row in rows]

    def _query(self, sqlite_sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        if self._sqlite:
            with closing(sqlite3.connect(self._database)) as connection:
                return connection.execute(sqlite_sql, params).fetchall()
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(sqlite_sql.replace("?", "%s").replace("payload", "payload::text"), params)
            return cursor.fetchall()


class ArtifactStore:
    """Guarda bytes na base de dados e expõe apenas IDs/metadata às tools."""

    def __init__(self, database: str | Path | None = None):
        self._database = str(database) if database else None
        self._sqlite = _sqlite_path(database) is not None

    def put_bytes(self, filename: str, data: bytes, mime_type: str = "application/octet-stream") -> dict[str, Any]:
        self._ensure()
        artifact_id = str(uuid4())
        digest = hashlib.sha256(data).hexdigest()
        if self._sqlite:
            with closing(sqlite3.connect(self._database)) as connection, connection:
                connection.execute(
                    "INSERT INTO artifacts VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (artifact_id, filename, mime_type, data, digest, len(data)),
                )
        else:
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO artifacts(artifact_id,filename,mime_type,content,sha256,size_bytes) VALUES (%s,%s,%s,%s,%s,%s)",
                    (artifact_id, filename, mime_type, data, digest, len(data)),
                )
        return {"artifact_id": artifact_id, "name": filename, "filename": filename, "type": mime_type,
                "mime_type": mime_type, "size_bytes": len(data), "sha256": digest}

    def get_bytes(self, artifact_id: str) -> bytes:
        self._ensure()
        rows = self._artifact_query("SELECT content FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        if not rows:
            raise FileNotFoundError(f"Artefacto inexistente: {artifact_id}")
        return bytes(rows[0][0])

    def get_url(self, artifact_id: str) -> str:
        self.get_bytes(artifact_id)
        return f"db://artifacts/{artifact_id}"

    def register(self, artifact_id: str, path: str, mime_type: str) -> ArtifactRecord:
        data = Path(path).read_bytes()
        metadata = self.put_bytes(Path(path).name, data, mime_type)
        return ArtifactRecord(
            filename=metadata["filename"], path=f"db://artifacts/{metadata['artifact_id']}",
            mime_type=mime_type, sha256=metadata["sha256"], size_bytes=metadata["size_bytes"],
        )

    def list_versions(self, artifact_id: str) -> list[ArtifactRecord]:
        rows = self._artifact_query(
            "SELECT filename,mime_type,sha256,size_bytes,artifact_id FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        )
        return [ArtifactRecord(filename=r[0], path=f"db://artifacts/{r[4]}", mime_type=r[1], sha256=r[2], size_bytes=r[3]) for r in rows]

    def _artifact_query(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        if self._sqlite:
            with closing(sqlite3.connect(self._database)) as connection:
                return connection.execute(sql, params).fetchall()
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.replace("?", "%s"), params)
            return cursor.fetchall()

    def _ensure(self) -> None:
        if self._sqlite:
            _init_sqlite(self._database)
        else:
            try:
                _ensure_table("artifacts", """
                    artifact_id TEXT PRIMARY KEY, filename TEXT NOT NULL, mime_type TEXT NOT NULL,
                    content BYTEA NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                """)
            except Exception as error:
                logger.warning("Postgres indisponível para ArtifactStore (%s); a usar fallback SQLite.", error)
                self._database = FALLBACK_SQLITE_PATH
                self._sqlite = True
                _init_sqlite(self._database)


class QuotaExceededError(RuntimeError):
    pass


class UserQuotaStore:
    def __init__(self, database: str | Path | None = None, *, max_operations: int = 100, window_seconds: int = 86400):
        self.max_operations, self.window_seconds = max_operations, window_seconds
        self._database = str(database) if database else None
        self._sqlite = _sqlite_path(database) is not None
        if self._sqlite:
            _init_sqlite(self._database)
        else:
            try:
                _ensure_table("user_quota_events", "user_id TEXT NOT NULL, operation TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL")
            except Exception as error:
                logger.warning("Postgres indisponível para UserQuotaStore (%s); a usar fallback SQLite.", error)
                self._database = FALLBACK_SQLITE_PATH
                self._sqlite = True
                _init_sqlite(self._database)

    def consume(self, user_id: str, operation: str) -> None:
        if not user_id:
            raise ValueError("É necessário identificar o utilizador para aplicar quotas.")
        cutoff = time.time() - self.window_seconds
        if self._sqlite:
            with closing(sqlite3.connect(self._database)) as connection, connection:
                connection.execute("DELETE FROM user_quota_events WHERE created_at < ?", (cutoff,))
                count = connection.execute("SELECT COUNT(*) FROM user_quota_events WHERE user_id = ? AND created_at >= ?", (user_id, cutoff)).fetchone()[0]
                if count >= self.max_operations:
                    raise QuotaExceededError(f"Quota diária excedida para o utilizador ({self.max_operations} operações).")
                connection.execute("INSERT INTO user_quota_events VALUES (?,?,?)", (user_id, operation, time.time()))
            return
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_quota_events WHERE created_at < %s", (cutoff,))
            cursor.execute("SELECT COUNT(*) FROM user_quota_events WHERE user_id = %s AND created_at >= %s", (user_id, cutoff))
            if cursor.fetchone()[0] >= self.max_operations:
                raise QuotaExceededError(f"Quota diária excedida para o utilizador ({self.max_operations} operações).")
            cursor.execute("INSERT INTO user_quota_events VALUES (%s,%s,%s)", (user_id, operation, time.time()))


_sync_context: AbstractContextManager | None = None
_sync_saver: PostgresSaver | None = None
_async_context: AbstractAsyncContextManager | None = None
_async_saver: AsyncPostgresSaver | None = None


def create_checkpoint_saver() -> PostgresSaver:
    global _sync_context, _sync_saver
    if _sync_saver is None:
        _sync_context = PostgresSaver.from_conn_string(database_url())
        _sync_saver = _sync_context.__enter__()
        _sync_saver.setup()
    return _sync_saver


async def create_async_checkpoint_saver() -> AsyncPostgresSaver:
    global _async_context, _async_saver
    if _async_saver is None:
        _async_context = AsyncPostgresSaver.from_conn_string(database_url())
        _async_saver = await _async_context.__aenter__()
        await _async_saver.setup()
    return _async_saver


def get_checkpoint_saver(*, fallback_to_memory: bool = False) -> BaseCheckpointSaver:
    try:
        return create_checkpoint_saver()
    except Exception:
        if fallback_to_memory:
            return MemorySaver()
        raise


def _ensure_table(name: str, columns: str) -> None:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")


def _init_sqlite(database: str | None) -> None:
    if not database:
        raise RuntimeError("É necessário configurar uma base de dados SQLite.")
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS workflow_runs (
            request_id TEXT PRIMARY KEY, workflow TEXT NOT NULL, user_id TEXT NOT NULL,
            status TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        connection.execute("CREATE TABLE IF NOT EXISTS user_quota_events (user_id TEXT, operation TEXT, created_at REAL)")
        connection.execute("""CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY, filename TEXT, mime_type TEXT, content BLOB,
            sha256 TEXT, size_bytes INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
