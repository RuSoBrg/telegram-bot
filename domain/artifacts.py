import hashlib
from pathlib import Path

from domain.database import connect
from domain.models import ArtifactRecord


class ArtifactVersionStore:
    """Regista versões imutáveis dos artefactos no PostgreSQL."""

    def __init__(self, _database_url: str | None = None):
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
