from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domain.db import connect


class AvailabilityStore:
    def __init__(self, location: str | None = None, ttl_seconds: float = 15):
        configured = (
            location
            or (
                os.getenv("DATABASE_URL")
                if (os.getenv("DATABASE_URL") or "").startswith(
                    ("postgresql://", "postgres://")
                )
                else None
            )
            or os.getenv("MODEL_AVAILABILITY_DB_PATH")
            or "data/model_availability.db"
        )
        self._location = configured
        self._sqlite = not configured.startswith(("postgresql://", "postgres://"))
        self._ttl = ttl_seconds
        self._cache: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}
        self._ensure_table()

    def _ensure_table(self) -> None:
        if self._sqlite:
            Path(self._location).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._location)) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(model_availability)"
                    ).fetchall()
                }
                if "model" in columns and "provider" not in columns:
                    connection.execute(
                        "ALTER TABLE model_availability RENAME TO model_availability_legacy"
                    )
        statement = """
            CREATE TABLE IF NOT EXISTS model_availability (
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                unavailable_until TIMESTAMPTZ NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                fail_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(provider, model)
            )
        """
        if self._sqlite:
            with closing(sqlite3.connect(self._location)) as connection:
                with connection:
                    connection.execute(statement.replace("TIMESTAMPTZ", "TEXT"))
        else:
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'model_availability' AND column_name = 'model'
                    """
                )
                legacy = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'model_availability' AND column_name = 'provider'
                    """
                )
                if legacy and not cursor.fetchone():
                    cursor.execute(
                        "ALTER TABLE model_availability RENAME TO model_availability_legacy"
                    )
                cursor.execute(statement)

    def _read(self, provider: str, model: str) -> dict[str, Any] | None:
        key = (provider, model)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
        if self._sqlite:
            with closing(sqlite3.connect(self._location)) as connection:
                row = connection.execute(
                    "SELECT provider, model, unavailable_until, reason, last_error, fail_count, updated_at "
                    "FROM model_availability WHERE provider = ? AND model = ?",
                    key,
                ).fetchone()
        else:
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT provider, model, unavailable_until, reason, last_error, fail_count, updated_at "
                    "FROM model_availability WHERE provider = %s AND model = %s",
                    key,
                )
                row = cursor.fetchone()
        value = None
        if row:
            value = dict(
                zip(
                    ("provider", "model", "unavailable_until", "reason", "last_error", "fail_count", "updated_at"),
                    row,
                )
            )
        self._cache[key] = (time.monotonic(), value)
        return value

    def is_available(self, provider: str, model: str) -> bool:
        row = self._read(provider, model)
        if not row:
            return True
        until = row["unavailable_until"]
        if isinstance(until, str):
            until = datetime.fromisoformat(until)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return until <= datetime.now(timezone.utc)

    def mark_unavailable(
        self,
        provider: str,
        model: str,
        until: datetime,
        reason: str,
        error: str,
    ) -> None:
        previous = self._read(provider, model)
        fail_count = int(previous["fail_count"]) + 1 if previous else 1
        values = (provider, model, until, reason, error, fail_count)
        if self._sqlite:
            with closing(sqlite3.connect(self._location)) as connection:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO model_availability
                            (provider, model, unavailable_until, reason, last_error, fail_count)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(provider, model) DO UPDATE SET
                            unavailable_until=excluded.unavailable_until,
                            reason=excluded.reason,
                            last_error=excluded.last_error,
                            fail_count=excluded.fail_count,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (*values[:2], until.isoformat(), *values[3:]),
                    )
        else:
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO model_availability
                        (provider, model, unavailable_until, reason, last_error, fail_count)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT(provider, model) DO UPDATE SET
                        unavailable_until=excluded.unavailable_until,
                        reason=excluded.reason,
                        last_error=excluded.last_error,
                        fail_count=excluded.fail_count,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    values,
                )
        self._cache.pop((provider, model), None)

    def mark_success(self, provider: str, model: str) -> None:
        if self._sqlite:
            with closing(sqlite3.connect(self._location)) as connection:
                with connection:
                    connection.execute(
                        "DELETE FROM model_availability WHERE provider = ? AND model = ?",
                        (provider, model),
                    )
        else:
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM model_availability WHERE provider = %s AND model = %s",
                    (provider, model),
                )
        self._cache.pop((provider, model), None)

    def failure_count(self, provider: str, model: str) -> int:
        row = self._read(provider, model)
        return int(row["fail_count"]) if row else 0

    def snapshot(self) -> list[dict[str, Any]]:
        if self._sqlite:
            with closing(sqlite3.connect(self._location)) as connection:
                rows = connection.execute(
                    "SELECT provider, model, unavailable_until, reason, last_error, fail_count, updated_at "
                    "FROM model_availability"
                ).fetchall()
        else:
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT provider, model, unavailable_until, reason, last_error, fail_count, updated_at "
                    "FROM model_availability"
                )
                rows = cursor.fetchall()
        names = ("provider", "model", "unavailable_until", "reason", "last_error", "fail_count", "updated_at")
        return [dict(zip(names, row)) for row in rows]

    def clear_expired(self) -> int:
        now = datetime.now(timezone.utc)
        if self._sqlite:
            with closing(sqlite3.connect(self._location)) as connection:
                with connection:
                    cursor = connection.execute(
                        "DELETE FROM model_availability WHERE unavailable_until <= ?",
                        (now.isoformat(),),
                    )
                    count = cursor.rowcount
        else:
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM model_availability WHERE unavailable_until <= %s",
                    (now,),
                )
                count = cursor.rowcount
        self._cache.clear()
        return count
