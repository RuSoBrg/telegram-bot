import asyncio

from langchain_core.messages import AIMessage

from app.service import WorkflowService
from app.settings import Settings


def test_settings_parse_telegram_ids_from_csv(monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "12345678, 87654321")

    settings = Settings()

    assert settings.allowed_telegram_user_ids == [12345678, 87654321]


def test_service_collects_response_artifacts_approval_and_errors():
    async def events():
        yield {
            "status": "failed",
            "errors": ["falha de teste"],
            "messages": [AIMessage(content="Resposta parcial")],
        }

    result = asyncio.run(WorkflowService().collect(events()))

    assert result.text == "Resposta parcial"
    assert result.errors == ["falha de teste"]
