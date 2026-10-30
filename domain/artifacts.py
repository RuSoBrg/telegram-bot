import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from domain.models import ArtifactRecord


class ArtifactVersionStore:
    """Regista versões imutáveis dos artefactos produzidos."""

    def __init__(self, path: str | Path = "workspace.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
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
            connection.commit()

    def register(self, artifact_id: str, path: str, mime_type: str) -> ArtifactRecord:
        artifact_path = Path(path).resolve()
        if not artifact_path.is_file():
            raise FileNotFoundError(f"Artefacto inexistente: {path}")
        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        artifact = ArtifactRecord(
            filename=artifact_path.name,
            path=str(artifact_path),
            mime_type=mime_type,
            sha256=digest,
            size_bytes=artifact_path.stat().st_size,
        )
        with closing(sqlite3.connect(self.path)) as connection:
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM artifact_versions WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()[0]
            with connection:
                connection.execute(
                    "INSERT INTO artifact_versions(artifact_id, version, payload) VALUES (?, ?, ?)",
                    (artifact_id, version, artifact.model_dump_json()),
                )
        return artifact

    def list_versions(self, artifact_id: str) -> list[ArtifactRecord]:
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT payload FROM artifact_versions WHERE artifact_id = ? ORDER BY version",
                (artifact_id,),
            ).fetchall()
        return [ArtifactRecord.model_validate_json(row[0]) for row in rows]
