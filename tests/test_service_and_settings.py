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
    assert result.failed is True


def test_service_extracts_all_tool_message_artifacts():
    from langchain_core.messages import ToolMessage
    import json

    art1 = json.dumps({"tipo": "ficheiro_gerado", "filename": "doc1.pdf", "size_bytes": 100, "uri": "db://artifacts/1"})
    art2 = json.dumps({"tipo": "ficheiro_gerado", "filename": "plan2.xlsx", "size_bytes": 200, "uri": "db://artifacts/2"})
    art3 = json.dumps({"tipo": "ficheiro_gerado", "filename": "slides3.pptx", "size_bytes": 300, "uri": "db://artifacts/3"})

    async def events():
        yield {
            "messages": [
                ToolMessage(content=art1, tool_call_id="call_1"),
                ToolMessage(content=art2, tool_call_id="call_2"),
                ToolMessage(content=art3, tool_call_id="call_3"),
            ],
            "status": "completed",
        }

    result = asyncio.run(WorkflowService().collect(events()))
    assert len(result.artifacts) == 3
    filenames = [a["filename"] for a in result.artifacts]
    assert filenames == ["doc1.pdf", "plan2.xlsx", "slides3.pptx"]
    assert result.failed is False
