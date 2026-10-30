from abc import ABC, abstractmethod
import os
from urllib.parse import quote


class ObjectStore(ABC):
    """Contrato de armazenamento de artefactos sem expor o sistema de ficheiros."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> str:
        """Guarda bytes e devolve a URI pública ou identificador do objecto."""

    @abstractmethod
    def get_url(self, key: str) -> str:
        """Devolve a URI utilizável para obter o objecto."""


class InMemoryObjectStore(ObjectStore):
    def __init__(self, base_url: str = "memory://artifacts"):
        self.base_url = base_url.rstrip("/")
        self._objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes) -> str:
        _validate_key(key)
        self._objects[key] = bytes(data)
        return self.get_url(key)

    def get_url(self, key: str) -> str:
        _validate_key(key)
        if key not in self._objects:
            raise FileNotFoundError(f"Objecto inexistente: {key}")
        return f"{self.base_url}/{quote(key)}"

    def get_bytes(self, key: str) -> bytes:
        _validate_key(key)
        return self._objects[key]


class GCSObjectStore(ObjectStore):
    def __init__(self, bucket_name: str, prefix: str = ""):
        if not bucket_name:
            raise ValueError("GCS_BUCKET é obrigatório para o backend GCS.")
        from google.cloud import storage

        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.prefix = prefix.strip("/")

    def _blob_name(self, key: str) -> str:
        _validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put_bytes(self, key: str, data: bytes) -> str:
        blob_name = self._blob_name(key)
        blob = self.bucket.blob(blob_name)
        blob.upload_from_string(data)
        return f"gs://{self.bucket.name}/{blob_name}"

    def get_url(self, key: str) -> str:
        blob_name = self._blob_name(key)
        return f"https://storage.googleapis.com/{self.bucket.name}/{quote(blob_name)}"

    def get_bytes(self, key: str) -> bytes:
        return self.bucket.blob(self._blob_name(key)).download_as_bytes()


def _validate_key(key: str) -> None:
    if not key or key.startswith("/") or ".." in key.replace("\\", "/").split("/"):
        raise ValueError("object key inválida.")


def create_object_store() -> ObjectStore:
    backend = os.getenv("OBJECT_STORAGE_BACKEND", "memory").lower()
    if backend == "gcs":
        return GCSObjectStore(
            os.getenv("GCS_BUCKET", ""),
            os.getenv("GCS_PREFIX", ""),
        )
    if backend != "memory":
        raise ValueError(
            "OBJECT_STORAGE_BACKEND deve ser 'memory' ou 'gcs'; armazenamento local removido."
        )
    return InMemoryObjectStore(os.getenv("OBJECT_STORAGE_URL", "memory://artifacts"))
