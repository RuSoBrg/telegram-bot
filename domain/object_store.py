import hashlib
import os
import shutil
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    def put(self, source_path: str | Path, object_key: str) -> dict[str, str | int]:
        ...

    def get_path(self, object_key: str) -> Path:
        ...


class ObjectStore:
    """Armazenamento de artefactos com backend local e opção S3 compatível."""

    def __init__(self, root: str | Path = "object_storage"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, source_path: str | Path, object_key: str) -> dict[str, str | int]:
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Artefacto inexistente: {source}")
        if not object_key or Path(object_key).is_absolute() or ".." in Path(object_key).parts:
            raise ValueError("object_key inválida.")
        target = (self.root / object_key).resolve()
        if self.root.resolve() not in target.parents:
            raise ValueError("object_key fora do armazenamento permitido.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return {
            "object_key": object_key,
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }

    def get_path(self, object_key: str) -> Path:
        target = (self.root / object_key).resolve()
        if self.root.resolve() not in target.parents or not target.is_file():
            raise FileNotFoundError(f"Objecto inexistente: {object_key}")
        return target


class GCSObjectStore:
    def __init__(self, bucket_name: str, prefix: str = ""):
        if not bucket_name:
            raise ValueError("GCS_BUCKET é obrigatório para o backend GCS.")
        from google.cloud import storage

        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.prefix = prefix.strip("/")

    def _blob_name(self, object_key: str) -> str:
        if not object_key or Path(object_key).is_absolute() or ".." in Path(object_key).parts:
            raise ValueError("object_key inválida.")
        return f"{self.prefix}/{object_key}" if self.prefix else object_key

    def put(self, source_path: str | Path, object_key: str) -> dict[str, str | int]:
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Artefacto inexistente: {source}")
        blob_name = self._blob_name(object_key)
        blob = self.bucket.blob(blob_name)
        blob.upload_from_filename(str(source))
        return {
            "object_key": object_key,
            "uri": f"gs://{self.bucket.name}/{blob_name}",
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }

    def get_path(self, object_key: str) -> Path:
        raise RuntimeError(
            "O backend GCS não expõe caminhos locais; use o URI gs:// para download."
        )


def create_object_store() -> ArtifactStore:
    backend = os.getenv("OBJECT_STORAGE_BACKEND", "local").lower()
    if backend == "gcs":
        return GCSObjectStore(
            os.getenv("GCS_BUCKET", ""),
            os.getenv("GCS_PREFIX", ""),
        )
    if backend != "local":
        raise ValueError(f"OBJECT_STORAGE_BACKEND inválido: {backend}")
    return ObjectStore(os.getenv("OBJECT_STORAGE_PATH", "object_storage"))
