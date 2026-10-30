import json
import unittest
from pathlib import Path

from tools.file_tools import (
    gerar_apresentacao_powerpoint,
    gerar_documento_pdf,
    gerar_documento_word,
)


class FileToolsTests(unittest.TestCase):
    def tearDown(self):
        from tools.file_tools import GENERATED_DIR

        for path in GENERATED_DIR.glob("test-*.docx"):
            path.unlink()
        for path in GENERATED_DIR.glob("test-*.pptx"):
            path.unlink()
        for path in GENERATED_DIR.glob("test-*.pdf"):
            path.unlink()

    def _assert_generated(self, result: str, suffix: str):
        artifact = json.loads(result)
        self.assertEqual(artifact["tipo"], "ficheiro_gerado")
        path = Path(artifact["path"])
        self.assertEqual(path.suffix, suffix)
        self.assertTrue(path.is_file())

    def test_generates_word(self):
        self._assert_generated(
            gerar_documento_word.invoke(
                {"conteudo": "# Plano\n- Primeiro passo", "nome_ficheiro": "test-word"}
            ),
            ".docx",
        )

    def test_generates_powerpoint(self):
        self._assert_generated(
            gerar_apresentacao_powerpoint.invoke(
                {"conteudo": "Resumo\n\nPonto 1\nPonto 2", "nome_ficheiro": "test-slides"}
            ),
            ".pptx",
        )

    def test_generates_pdf(self):
        self._assert_generated(
            gerar_documento_pdf.invoke(
                {"conteudo": "# Relatório\nConteúdo", "nome_ficheiro": "test-pdf"}
            ),
            ".pdf",
        )


if __name__ == "__main__":
    unittest.main()
