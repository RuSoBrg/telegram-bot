import json
from pathlib import Path
from typing import Any


CASES_PATH = Path(__file__).with_name("training_cases.json")


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_all_cases(directory: Path | None = None) -> list[dict[str, Any]]:
    root = directory or CASES_PATH.parent
    cases: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        cases.extend(load_cases(path))
    return cases


def evaluate_training_output(output: str, case: dict[str, Any]) -> dict[str, Any]:
    """Avalia cobertura mínima sem chamar modelos nem consumir APIs."""
    normalized = output.casefold()
    required = case.get("required", [])
    missing = [item for item in required if item.casefold() not in normalized]
    score = round((len(required) - len(missing)) / len(required) * 100) if required else 0
    return {
        "name": case.get("name", ""),
        "passed": not missing and score >= case.get("minimum_quality_score", 85),
        "score": score,
        "missing": missing,
    }


def evaluate_all(
    outputs: dict[str, str],
    cases: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return [
        evaluate_training_output(outputs.get(case["name"], ""), case)
        for case in cases or load_all_cases()
    ]
