import json

from langchain_core.messages import AIMessage, ToolMessage

from domain.events import AppEvent, iter_app_events
from domain.db import ArtifactStore
from tools import file_tools


def test_zip_is_generated_in_database(monkeypatch, tmp_path):
    store = ArtifactStore(tmp_path / "artifacts.db")
    monkeypatch.setattr(file_tools, "artifact_store", store)
    source = store.put_bytes("source.txt", b"conteudo", "text/plain")

    result = json.loads(
        file_tools.empacotar_artefactos.invoke(
            {"caminhos": [source["artifact_id"]], "nome_ficheiro": "pacote"}
        )
    )

    assert result["tipo"] == "ficheiro_gerado"
    assert result["artifact_id"]
    assert store.get_bytes(result["artifact_id"])
    assert source["artifact_id"] == store.get_url(source["artifact_id"]).rsplit("/", 1)[-1]


def test_graph_events_are_normalized_to_app_events():
    events = list(
        iter_app_events(
            [
                {"messages": [AIMessage(content="resposta")], "status": "producing"},
                {
                    "messages": [
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "tipo": "ficheiro_gerado",
                                    "filename": "doc.pdf",
                                    "uri": "memory://artifacts/doc.pdf",
                                }
                            ),
                            tool_call_id="tool-1",
                        )
                    ]
                },
                {"__interrupt__": "approval"},
            ]
        )
    )

    assert all(isinstance(event, AppEvent) for event in events)
    assert [event.type for event in events] == ["message", "artifact", "status"]
    assert events[0].content == "resposta"
    assert events[1].metadata["uri"].endswith("doc.pdf")
