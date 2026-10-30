import tempfile
import unittest
from pathlib import Path

from domain.artifacts import ArtifactVersionStore
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
            file_path.write_text("v1", encoding="utf-8")
            store = ArtifactVersionStore(Path(directory) / "artifacts.db")
            store.register("lesson", str(file_path), "text/plain")
            file_path.write_text("v2", encoding="utf-8")
            store.register("lesson", str(file_path), "text/plain")
            versions = store.list_versions("lesson")
            self.assertEqual(len(versions), 2)
            self.assertNotEqual(versions[0].sha256, versions[1].sha256)

    def test_timed_event_completes_without_error(self):
        with timed_event("test_event", request_id="test-request", agent="test"):
            pass
