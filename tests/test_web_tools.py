import json
import os
import unittest
from unittest.mock import patch

from tools.web_tools import pesquisar_web


class WebToolsTests(unittest.TestCase):
    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = json.loads(pesquisar_web.invoke({"consulta": "Flutter"}))

        self.assertIn("TAVILY_API_KEY", result["erro"])

    @patch("tavily.TavilyClient")
    def test_returns_sources(self, tavily_client):
        tavily_client.return_value.search.return_value = {
            "answer": "Resumo",
            "results": [
                {
                    "title": "Documentação oficial",
                    "url": "https://example.com/docs",
                    "content": "Conteúdo relevante",
                }
            ],
        }
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            result = json.loads(pesquisar_web.invoke({"consulta": "Flutter"}))

        self.assertEqual(result["resumo"], "Resumo")
        self.assertEqual(result["fontes"][0]["url"], "https://example.com/docs")
        tavily_client.assert_called_once_with(api_key="test-key")


if __name__ == "__main__":
    unittest.main()
