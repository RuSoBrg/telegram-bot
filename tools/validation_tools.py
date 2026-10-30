import json
from pathlib import Path

from langchain_core.tools import tool

from domain.models import ArtifactRecord


GENERATED_DIR = (Path(__file__).resolve().parent.parent / "generated_files").resolve()


@tool
def validar_artefactos(paths: list[str]) -> str:
    """Valida que os ficheiros gerados existem, têm tamanho e extensões suportadas."""
    allowed = {".docx", ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".mp3", ".wav", ".mp4"}
    artefacts: list[ArtifactRecord] = []
    errors: list[str] = []
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if GENERATED_DIR not in path.parents:
            errors.append(f"Caminho fora da pasta de artefactos: {path.name}")
            continue
        if path.suffix.lower() not in allowed:
            errors.append(f"Extensão não suportada: {path.name}")
        elif not path.is_file():
            errors.append(f"Ficheiro inexistente: {path.name}")
        elif path.stat().st_size == 0:
            errors.append(f"Ficheiro vazio: {path.name}")
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
            }[path.suffix.lower()]
            artefacts.append(ArtifactRecord(filename=path.name, path=str(path), mime_type=mime))
    return json.dumps(
        {"valid": not errors, "artefacts": [item.model_dump(mode="json") for item in artefacts], "errors": errors},
        ensure_ascii=False,
    )
