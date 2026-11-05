from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")
CATALOG_PATH = Path(__file__).with_name("catalog.yaml")
load_dotenv()


@dataclass(frozen=True)
class ModelEntry:
    provider: str
    model: str
    api_key_env: str
    capabilities: tuple[str, ...]
    timeout_s: float
    score: int
    api_base_env: str | None = None
    api_version_env: str | None = None
    base_url_env: str | None = None

    @property
    def model_id(self) -> str:
        return f"{self.provider}/{self.model}"


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda match: os.getenv(match.group(1), match.group(2) or ""),
            value,
        )
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, list[ModelEntry]]:
    data = _expand(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    tasks = data.get("tasks", {})
    result: dict[str, list[ModelEntry]] = {}
    for task, entries in tasks.items():
        result[task] = [
            ModelEntry(
                provider=str(item["provider"]),
                model=str(item["model"]),
                api_key_env=str(item.get("api_key_env", "")),
                capabilities=tuple(item.get("capabilities", [])),
                timeout_s=float(item.get("timeout_s", 60)),
                score=int(item.get("score", 0)),
                api_base_env=item.get("api_base_env"),
                api_version_env=item.get("api_version_env"),
                base_url_env=item.get("base_url_env"),
            )
            for item in entries
            if _configured(item)
        ]
    return result


def _configured(item: dict[str, Any]) -> bool:
    key_env = str(item.get("api_key_env", ""))
    if key_env and os.getenv(key_env, "").strip():
        return True
    base_env = item.get("base_url_env")
    return bool(base_env and os.getenv(str(base_env), "").strip())
