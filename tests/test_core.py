import json

from langchain_core.messages import AIMessage, ToolMessage

from domain.events import AppEvent, iter_app_events
from domain.object_store import InMemoryObjectStore
from tools import file_tools


def test_zip_is_generated_in_memory(monkeypatch):
    store = InMemoryObjectStore()
    monkeypatch.setattr(file_tools, "store", store)
    source_uri = store.put_bytes("source.txt", b"conteudo")

    result = json.loads(
        file_tools.empacotar_artefactos.invoke(
            {"caminhos": ["source.txt"], "nome_ficheiro": "pacote"}
        )
    )

    assert result["tipo"] == "ficheiro_gerado"
    assert result["uri"].startswith("memory://")
    assert store.get_bytes(result["filename"])
    assert source_uri == store.get_url("source.txt")


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
