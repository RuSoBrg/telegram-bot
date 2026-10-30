from abc import ABC, abstractmethod
import os
from pathlib import Path
from urllib.parse import quote


class ObjectStore(ABC):
    """Contrato de armazenamento local de artefactos."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> str:
        """Guarda bytes e devolve o caminho local do objecto."""

    @abstractmethod
    def get_url(self, key: str) -> str:
        """Devolve o identificador local utilizável para obter o objecto."""


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


class LocalObjectStore(ObjectStore):
    def __init__(self, root: str | Path = "/app/data"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        _validate_key(key)
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("object key inválida.")
        return path

    def put_bytes(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def get_url(self, key: str) -> str:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"Objecto inexistente: {key}")
        return str(path)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()


def _validate_key(key: str) -> None:
    if not key or key.startswith("/") or ".." in key.replace("\\", "/").split("/"):
        raise ValueError("object key inválida.")


def create_object_store() -> ObjectStore:
    return LocalObjectStore(os.getenv("OBJECT_STORAGE_PATH", "/app/data"))
