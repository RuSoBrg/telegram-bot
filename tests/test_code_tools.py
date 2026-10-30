import unittest
from pathlib import Path

from tools.code_tools import _resolve_allowed_path, ler_ficheiro_codigo


class CodeToolsSecurityTests(unittest.TestCase):
    def test_resolves_workspace_relative_file(self):
        caminho = _resolve_allowed_path("tools/code_tools.py")

        self.assertEqual(caminho.name, "code_tools.py")
        self.assertTrue(caminho.is_file())

    def test_blocks_sensitive_file(self):
        with self.assertRaises(PermissionError):
            _resolve_allowed_path(".env")

    def test_blocks_path_outside_allowed_roots(self):
        outside = Path.cwd().parent / "outside-agent-workspace.txt"

        with self.assertRaises(PermissionError):
            _resolve_allowed_path(str(outside))

    def test_file_tool_returns_safe_error_for_blocked_path(self):
        result = ler_ficheiro_codigo.invoke({"caminho_ficheiro": ".env"})

        self.assertEqual(result, "Erro: o acesso a este caminho não é permitido.")


if __name__ == "__main__":
    unittest.main()
