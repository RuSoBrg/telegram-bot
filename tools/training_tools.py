import json

from langchain_core.tools import tool
from pydantic import ValidationError

from domain.models import TrainingPackage


@tool
def validar_pacote_formacao(pacote_json: str) -> str:
    """Valida um pacote de formação contra o contrato estruturado do sistema.
    O argumento deve ser um JSON com título, público, duração, objetivos, plano,
    avaliação e recursos. Usa antes de gerar os ficheiros finais."""
    try:
        package = TrainingPackage.model_validate_json(pacote_json)
    except ValidationError as error:
        return json.dumps(
            {"valid": False, "errors": json.loads(error.json())},
            ensure_ascii=False,
        )
    except Exception as error:
        return json.dumps(
            {"valid": False, "erro": f"Tool execution failed: {error}"},
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "valid": True,
            "title": package.title,
            "objectives": len(package.learning_objectives),
            "activities": len(package.session_plan),
            "activity_details": len(package.activities),
            "assessment_items": len(package.assessment),
        },
        ensure_ascii=False,
    )
