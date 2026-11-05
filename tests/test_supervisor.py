from unittest.mock import patch

from agents.models import RoutingDecision
from agents.supervisor import create_supervisor_node


def test_supervisor_keeps_direct_answer_for_generalist() -> None:
    with (
        patch(
            "agents.supervisor.supervisor_model"
        ) as supervisor_model,
        patch("agents.supervisor.load_prompt", return_value="prompt"),
    ):
        supervisor_model.return_value.invoke.return_value = RoutingDecision(
            destino="generalista",
            resposta_ou_instrucao="Resposta direta do supervisor.",
        )
        command = create_supervisor_node()({"messages": [], "steps": 0})

    assert command.goto == "generalista"
    assert command.update["messages"][0].content == "Resposta direta do supervisor."
