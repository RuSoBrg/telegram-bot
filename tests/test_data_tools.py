import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.data_tools import analisar_ficheiro_dados, executar_query_sql
from tools.web_tools import raspar_pagina_web


class DataToolsTests(unittest.TestCase):
    def test_csv_returns_schema_and_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dados.csv"
            path.write_text("nome,valor\nA,10\nB,20\n", encoding="utf-8")

            result = json.loads(
                analisar_ficheiro_dados.invoke(
                    {"caminho": str(path), "limite_linhas": 1}
                )
            )

        self.assertEqual(result["linhas"], 2)
        self.assertEqual(result["colunas"], ["nome", "valor"])
        self.assertEqual(len(result["exemplos"]), 1)

    def test_missing_file_is_returned_as_tool_error(self):
        result = analisar_ficheiro_dados.invoke({"caminho": "missing.csv"})

        self.assertIn("Error executing tool analisar_ficheiro_dados", result)
        self.assertIn("Ficheiro não encontrado", result)

    def test_sql_tool_returns_rows_without_mutating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE items (name TEXT, value INTEGER)")
            connection.execute("INSERT INTO items VALUES ('A', 3)")
            connection.commit()
            connection.close()

            result = json.loads(
                executar_query_sql.invoke(
                    {
                        "caminho_base_dados": str(path),
                        "query": "SELECT name, value FROM items",
                    }
                )
            )

        self.assertEqual(result["linhas"], [{"name": "A", "value": 3}])

    def test_sql_tool_rejects_write_query(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.db"
            sqlite3.connect(path).close()

            result = executar_query_sql.invoke(
                {
                    "caminho_base_dados": str(path),
                    "query": "DROP TABLE items",
                }
            )

        self.assertIn("Error executing tool executar_query_sql", result)
        self.assertIn("operação SQL de leitura", result)

    def test_scraper_rejects_non_http_url(self):
        result = raspar_pagina_web.invoke({"url": "file:///tmp/page.html"})

        self.assertIn("Error executing tool raspar_pagina_web", result)
        self.assertIn("http ou https", result)


if __name__ == "__main__":
    unittest.main()
