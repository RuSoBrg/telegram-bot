import unittest

from agents.models import tools_for


class AgentToolPolicyTests(unittest.TestCase):
    def test_engineering_cannot_generate_media(self):
        names = {tool.name for tool in tools_for("engenharia")}
        self.assertIn("pesquisar_web", names)
        self.assertIn("ler_ficheiro_codigo", names)
        self.assertNotIn("gerar_video", names)

    def test_life_has_only_documents_and_research(self):
        names = {tool.name for tool in tools_for("vida")}
        self.assertEqual(
            names,
            {"pesquisar_web", "gerar_documento_word", "gerar_documento_pdf", "validar_artefactos"},
        )

    def test_formador_has_training_delivery_tools(self):
        names = {tool.name for tool in tools_for("formador")}
        self.assertIn("validar_pacote_formacao", names)
        self.assertIn("empacotar_artefactos", names)
        self.assertIn("gerar_apresentacao_powerpoint", names)
