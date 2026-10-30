import json
import unittest
from pathlib import Path

from tools.file_tools import GENERATED_DIR, empacotar_artefactos


class ArchiveToolTests(unittest.TestCase):
    def test_packages_generated_files(self):
        first = GENERATED_DIR / "test-archive-a.txt"
        second = GENERATED_DIR / "test-archive-b.txt"
        archive = None
        try:
            first.write_text("a", encoding="utf-8")
            second.write_text("b", encoding="utf-8")
            result = json.loads(
                empacotar_artefactos.invoke(
                    {"caminhos": [str(first), str(second)], "nome_ficheiro": "test-archive"}
                )
            )
            archive = Path(result["path"])
            self.assertTrue(archive.is_file())
            self.assertEqual(archive.suffix, ".zip")
        finally:
            first.unlink(missing_ok=True)
            second.unlink(missing_ok=True)
            if archive:
                archive.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
