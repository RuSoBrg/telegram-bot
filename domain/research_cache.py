import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


class ResearchCache:
    """Cache local TTL para reduzir pesquisas Tavily repetidas."""

    def __init__(self, path: str | Path = "research-cache.db", ttl_seconds: int = 3600):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.commit()

    @staticmethod
    def key(query: str, profile: str, domains: list[str] | None) -> str:
        value = json.dumps(
            {"query": query.strip(), "profile": profile, "domains": sorted(domains or [])},
            sort_keys=True,
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def get(self, cache_key: str) -> dict[str, Any] | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT payload, created_at FROM research_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row or time.time() - row[1] > self.ttl_seconds:
            return None
        return json.loads(row[0])

    def put(self, cache_key: str, payload: dict[str, Any]) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO research_cache(cache_key, payload, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload=excluded.payload, created_at=excluded.created_at
                    """,
                    (cache_key, json.dumps(payload, ensure_ascii=False), time.time()),
                )
