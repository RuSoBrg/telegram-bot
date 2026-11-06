from langchain_core.messages import AIMessage

from workflows.main_workflow import TOOLS_REQUIRING_APPROVAL, _run_tools


def test_read_only_engineering_tool_does_not_require_approval():
    assert "gerar_documento_excel" in TOOLS_REQUIRING_APPROVAL
    response = AIMessage(
        content="",
        tool_calls=[
            {"name": "ler_ficheiro_codigo", "args": {"path": "x"}, "id": "read-1"}
        ],
    )
    assert not any(
        call["name"] in TOOLS_REQUIRING_APPROVAL for call in response.tool_calls
    )


def test_rejected_pending_tools_receive_tool_messages():
    class FakeNode:
        def invoke(self, state):
            raise AssertionError("a ferramenta não devia correr")

    response = AIMessage(
        content="",
        tool_calls=[
            {"name": "gerar_documento_excel", "args": {}, "id": "excel-1"},
            {"name": "validar_artefactos", "args": {}, "id": "validate-1"},
        ],
    )
    command = _run_tools(
        FakeNode(),
        {
            "messages": [response],
            "steps": 0,
            "approval_decision": "rejected",
        },
        "engenharia",
    )
    assert command.goto == "engenharia"
    assert [message.content for message in command.update["messages"]] == [
        "Rejeitado pelo utilizador",
        "Rejeitado pelo utilizador",
    ]
    assert command.update["approval_decision"] == ""


def test_engineering_specialist_routes_read_only_and_write_tools_differently(
    monkeypatch,
):
    from agents import specialists

    tool_name = ["ler_ficheiro_codigo"]

    class FakeModel:
        def invoke(self, messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool_name[0],
                        "args": {},
                        "id": "call-1",
                    }
                ],
            )

    monkeypatch.setattr(specialists, "specialist_models", lambda _: FakeModel())
    monkeypatch.setattr(specialists, "load_prompt", lambda _: "prompt")
    monkeypatch.setattr(specialists, "prepare_messages_for_model", lambda x: x)
    read_node = specialists.create_specialist_node(
        "engenharia", "tools_engenharia", "qualidade_engenharia",
        approval_node="approval_engenharia", approval_tools={"gerar_documento_excel"},
    )
    assert read_node({"messages": [], "steps": 0}).goto == "tools_engenharia"

    tool_name[0] = "gerar_documento_excel"
    write_node = specialists.create_specialist_node(
        "engenharia", "tools_engenharia", "qualidade_engenharia",
        approval_node="approval_engenharia", approval_tools={"gerar_documento_excel"},
    )
    assert write_node({"messages": [], "steps": 0}).goto == "approval_engenharia"
