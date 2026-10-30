import hashlib
import json
import time
from typing import Any

from domain.database import connect


class ResearchCache:
    """Cache TTL no PostgreSQL para reduzir pesquisas repetidas."""

    def __init__(self, _database_url: str | None = None, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
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
