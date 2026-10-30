import os

import psycopg


def database_url() -> str:
    url = os.getenv("WORKFLOW_DB_PATH", "").strip()
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError(
            "WORKFLOW_DB_PATH deve conter uma connection string PostgreSQL."
        )
    return url


def connect():
    return psycopg.connect(database_url())
