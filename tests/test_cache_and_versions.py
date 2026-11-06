import tempfile
import unittest
from pathlib import Path

from domain.db import ArtifactStore as ArtifactVersionStore
from domain.research_cache import ResearchCache
from domain.observability import timed_event


class CacheAndVersionTests(unittest.TestCase):
    def test_research_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ResearchCache(Path(directory) / "cache.db")
            key = cache.key("Python", "technical", ["docs.python.org"])
            cache.put(key, {"fontes": [{"url": "https://docs.python.org"}]})
            self.assertEqual(cache.get(key)["fontes"][0]["url"], "https://docs.python.org")

    def test_artifact_versions_increment(self):
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / "artifact.txt"
            store = ArtifactVersionStore(Path(directory) / "artifacts.db")
            file_path.write_text("v1", encoding="utf-8")
            first = store.put_bytes(file_path.name, file_path.read_bytes(), "text/plain")
            file_path.write_text("v2", encoding="utf-8")
            second = store.put_bytes(file_path.name, file_path.read_bytes(), "text/plain")
            self.assertNotEqual(first["artifact_id"], second["artifact_id"])
            self.assertNotEqual(first["sha256"], second["sha256"])

    def test_timed_event_completes_without_error(self):
        with timed_event("test_event", request_id="test-request", agent="test"):
            pass
