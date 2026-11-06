from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from domain.db import connect


class ModelAvailabilityStore:
    """Persiste o cooldown dos deployments por fornecedor/modelo."""

    def __init__(self, location: str | None = None):
        configured = location or os.getenv("MODEL_AVAILABILITY_DB_PATH")
        self._location = configured
        self._sqlite = bool(configured and not configured.startswith(("postgresql://", "postgres://")))
        if self._sqlite:
            Path(configured).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(configured)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS model_availability (
                            model TEXT PRIMARY KEY,
                            unavailable_until TEXT NOT NULL
                        )
                        """
                    )
        elif configured or os.getenv("DATABASE_URL"):
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_availability (
                        model TEXT PRIMARY KEY,
                        unavailable_until TIMESTAMPTZ NOT NULL
                    )
                    """
                )

    def mark_unavailable(self, model: str, duration_seconds: int) -> datetime:
        unavailable_until = datetime.now(timezone.utc) + timedelta(
            seconds=max(1, duration_seconds)
        )
        if self._sqlite:
            with closing(sqlite3.connect(self._location)) as connection:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO model_availability(model, unavailable_until)
                        VALUES (?, ?)
                        ON CONFLICT(model) DO UPDATE SET
                            unavailable_until=excluded.unavailable_until
                        """,
                        (model, unavailable_until.isoformat()),
                    )
        elif self._location or os.getenv("DATABASE_URL"):
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO model_availability(model, unavailable_until)
                    VALUES (%s, %s)
                    ON CONFLICT(model) DO UPDATE SET
                        unavailable_until=excluded.unavailable_until
                    """,
                    (model, unavailable_until),
                )
        return unavailable_until

    def is_available(self, model: str) -> bool:
        if self._sqlite:
            with closing(sqlite3.connect(self._location)) as connection:
                row = connection.execute(
                    "SELECT unavailable_until FROM model_availability WHERE model = ?",
                    (model,),
                ).fetchone()
        elif self._location or os.getenv("DATABASE_URL"):
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT unavailable_until FROM model_availability WHERE model = %s",
                    (model,),
                )
                row = cursor.fetchone()
        else:
            return True
        if not row:
            return True
        value = row[0]
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value <= datetime.now(timezone.utc)
