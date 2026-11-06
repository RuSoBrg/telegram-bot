import hashlib
import json
from pathlib import PurePosixPath

from langchain_core.tools import tool

from domain.models import ArtifactRecord
from domain.db import ArtifactStore


artifact_store = ArtifactStore()


@tool
def validar_artefactos(paths: list[str]) -> str:
    """Valida artefactos através dos IDs persistidos na base de dados."""
    allowed = {
        ".docx",
        ".pptx",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".mp3",
        ".wav",
        ".mp4",
        ".xlsx",
        ".zip",
    }
    artefacts: list[ArtifactRecord] = []
    errors: list[str] = []
    for key in paths:
        name = PurePosixPath(key.split("?", 1)[0]).name
        suffix = PurePosixPath(name).suffix.lower()
        try:
            data = artifact_store.get_bytes(key)
        except (FileNotFoundError, KeyError):
            errors.append(f"Ficheiro inexistente: {name}")
            continue
        except Exception as error:
            errors.append(f"Tool execution failed for {name}: {error}")
            continue
        if suffix not in allowed:
            errors.append(f"Extensão não suportada: {name}")
        elif not data:
            errors.append(f"Ficheiro vazio: {name}")
        else:
            mime = {
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
                ".mp4": "video/mp4",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".zip": "application/zip",
            }[suffix]
            artefacts.append(
                ArtifactRecord(
                    artifact_id=key,
                    filename=name,
                    path=f"db://artifacts/{key}",
                    mime_type=mime,
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
    return json.dumps(
        {
            "valid": not errors,
            "artefacts": [item.model_dump(mode="json") for item in artefacts],
            "errors": errors,
        },
        ensure_ascii=False,
    )
