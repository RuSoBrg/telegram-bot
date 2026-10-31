import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from domain.database import connect


class ResearchCache:
    """Cache TTL com PostgreSQL em produção e SQLite para testes locais."""

    def __init__(self, _database_url: str | None = None, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self._database_url = str(_database_url) if _database_url else None
        self._sqlite = bool(self._database_url and not self._database_url.startswith(("postgresql://", "postgres://")))
        if self._sqlite:
            Path(self._database_url).parent.mkdir(parents=True, exist_ok=True)
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS research_cache (
                            cache_key TEXT PRIMARY KEY,
                            payload TEXT NOT NULL,
                            created_at REAL NOT NULL
                        )
                        """
                    )
            return
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS research_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                )
                """
            )

    @staticmethod
    def key(query: str, profile: str, domains: list[str] | None) -> str:
        value = json.dumps(
            {"query": query.strip(), "profile": profile, "domains": sorted(domains or [])},
            sort_keys=True,
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def get(self, cache_key: str) -> dict[str, Any] | None:
        self._ensure_schema()
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
                row = connection.execute(
                    "SELECT payload, created_at FROM research_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
            if not row or time.time() - row[1] > self.ttl_seconds:
                return None
            return json.loads(row[0])
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload::text, created_at
                FROM research_cache
                WHERE cache_key = %s
                """,
                (cache_key,),
            )
            row = cursor.fetchone()
        if not row or time.time() - row[1] > self.ttl_seconds:
            return None
        return json.loads(row[0])

    def put(self, cache_key: str, payload: dict[str, Any]) -> None:
        self._ensure_schema()
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO research_cache(cache_key, payload, created_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            payload=excluded.payload,
                            created_at=excluded.created_at
                        """,
                        (cache_key, json.dumps(payload, ensure_ascii=False), time.time()),
                    )
            return
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO research_cache(cache_key, payload, created_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload=excluded.payload,
                    created_at=excluded.created_at
                """,
                (cache_key, json.dumps(payload, ensure_ascii=False), time.time()),
            )
