import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from domain.database import connect
from domain.models import ArtifactRecord


class ArtifactVersionStore:
    """Regista versões imutáveis dos artefactos."""

    def __init__(self, _database_url: str | None = None):
        self._database_url = str(_database_url) if _database_url else None
        self._sqlite = bool(self._database_url and not self._database_url.startswith(("postgresql://", "postgres://")))
        if self._sqlite:
            Path(self._database_url).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._database_url)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS artifact_versions (
                            artifact_id TEXT NOT NULL,
                            version INTEGER NOT NULL,
                            payload TEXT NOT NULL,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (artifact_id, version)
                        )
                        """
                    )
            return
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_versions (
                    artifact_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (artifact_id, version)
                )
                """
            )

    def register(self, artifact_id: str, path: str, mime_type: str) -> ArtifactRecord:
        artifact_path = Path(path).resolve()
        if not artifact_path.is_file():
            raise FileNotFoundError(f"Artefacto inexistente: {path}")
        data = artifact_path.read_bytes()
        artifact = ArtifactRecord(
            filename=artifact_path.name,
            path=str(artifact_path),
            mime_type=mime_type,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
                with connection:
                    version = connection.execute(
                        """
                        SELECT COALESCE(MAX(version), 0) + 1
                        FROM artifact_versions
                        WHERE artifact_id = ?
                        """,
                        (artifact_id,),
                    ).fetchone()[0]
                    connection.execute(
                        """
                        INSERT INTO artifact_versions(artifact_id, version, payload)
                        VALUES (?, ?, ?)
                        """,
                        (artifact_id, version, artifact.model_dump_json()),
                    )
            return artifact.model_copy(update={"version": version})
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM artifact_versions
                WHERE artifact_id = %s
                """,
                (artifact_id,),
            )
            version = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO artifact_versions(artifact_id, version, payload)
                VALUES (%s, %s, %s::jsonb)
                """,
                (artifact_id, version, artifact.model_dump_json()),
            )
        return artifact

    def list_versions(self, artifact_id: str) -> list[ArtifactRecord]:
        if self._sqlite:
            with closing(sqlite3.connect(self._database_url)) as connection:
                rows = connection.execute(
                    """
                    SELECT payload
                    FROM artifact_versions
                    WHERE artifact_id = ?
                    ORDER BY version
                    """,
                    (artifact_id,),
                ).fetchall()
            return [ArtifactRecord.model_validate_json(row[0]) for row in rows]
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload::text
                FROM artifact_versions
                WHERE artifact_id = %s
                ORDER BY version
                """,
                (artifact_id,),
            )
            rows = cursor.fetchall()
        return [ArtifactRecord.model_validate_json(row[0]) for row in rows]
