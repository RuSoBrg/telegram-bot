import json
import os
import unittest
from unittest.mock import patch

from tools.web_tools import pesquisar_web


class ResearchProfileTests(unittest.TestCase):
    @patch("tavily.TavilyClient")
    def test_profile_applies_official_domains(self, client):
        client.return_value.search.return_value = {"results": []}
        with patch.dict(
            os.environ,
            {"TAVILY_API_KEY": "test-key", "RESEARCH_CACHE_ENABLED": "false"},
        ):
            pesquisar_web.invoke({"consulta": "Python", "perfil": "official_docs"})
        self.assertIn("docs.python.org", client.return_value.search.call_args.kwargs["include_domains"])

    def test_invalid_profile_is_explicit(self):
        result = json.loads(pesquisar_web.invoke({"consulta": "Python", "perfil": "unknown"}))
        self.assertIn("Perfil de pesquisa inválido", result["erro"])
