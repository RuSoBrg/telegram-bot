from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from agents.models import RoutingDecision
from agents.supervisor import create_supervisor_node
from routing.keyword_classifier import classify_request


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tenho um bug em Python", "engenharia"),
        ("Como faço deploy com Docker?", "engenharia"),
        ("Preciso de uma API em C#", "engenharia"),
        ("Ajuda com uma query SQL", "engenharia"),
        ("Como depuro Flutter?", "engenharia"),
        ("Ideias para o TikTok", "marketing"),
        ("Cria um anúncio para o RoastNRizz", "marketing"),
        ("Plano de growth e SEO", "marketing"),
        ("Calendário de conteúdo para redes sociais", "marketing"),
        ("Como melhorar a aquisição?", "marketing"),
        ("Quero mudar de carreira", "vida"),
        ("Organiza as minhas finanças", "vida"),
        ("Que carro devo comprar?", "vida"),
        ("Ajuda com burocracia", "vida"),
        ("Cria rotinas para a minha saúde", "vida"),
        ("Prepara uma sessão de formação", "formador"),
        ("Plano para um curso", "formador"),
        ("Como fazer uma avaliação pedagógica?", "formador"),
        ("Preciso de uma apresentação pedagógica", "formador"),
        ("Ajuda com o CCP", "formador"),
        ("O que posso fazer hoje?", "generalista"),
    ],
)
def test_keyword_classifier_always_returns_expected_destination(text, expected):
    assert classify_request(text) == expected


def test_supervisor_uses_keyword_fallback_when_llm_fails():
    with (
        patch("agents.supervisor.supervisor_model") as factory,
        patch("agents.supervisor.load_prompt", return_value="prompt"),
    ):
        factory.return_value.invoke.side_effect = RuntimeError("indisponível")
        command = create_supervisor_node()(
            {"messages": [HumanMessage(content="Tenho um bug em Python")], "steps": 0}
        )

    assert command.goto == "engenharia"
    assert command.update["next_agent"] == "engenharia"
    assert command.update["errors"]


def test_supervisor_routes_ambiguous_request_to_generalista():
    with (
        patch("agents.supervisor.supervisor_model") as factory,
        patch("agents.supervisor.load_prompt", return_value="prompt"),
    ):
        factory.return_value.invoke.return_value = RoutingDecision(
            destino="generalista",
            resposta_ou_instrucao="Ajuda geral.",
        )
        command = create_supervisor_node()(
            {"messages": [HumanMessage(content="Ajuda geral")], "steps": 0}
        )

    assert command.goto == "generalista"
