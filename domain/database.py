import os

import psycopg


def database_url() -> str:
    url = (os.getenv("DATABASE_URL") or os.getenv("WORKFLOW_DB_PATH") or "").strip()
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError(
            "DATABASE_URL deve conter uma connection string PostgreSQL."
        )
    return url


def connect():
    return psycopg.connect(database_url())
