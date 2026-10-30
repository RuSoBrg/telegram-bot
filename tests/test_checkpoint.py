import os
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint

from domain.checkpoint import create_checkpoint_saver


class CheckpointTests(unittest.TestCase):
    def test_checkpoint_survives_new_saver_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoints.db"
            with self.subTest("write"):
                with patch_env("CHECKPOINT_DB_PATH", str(path)):
                    first = create_checkpoint_saver()
                    config = {"configurable": {"thread_id": "resume-test", "checkpoint_ns": ""}}
                    first.put(config, empty_checkpoint(), {}, {})
                    first.conn.close()
            with patch_env("CHECKPOINT_DB_PATH", str(path)):
                second = create_checkpoint_saver()
                self.assertIsNotNone(
                    second.get_tuple(
                        {"configurable": {"thread_id": "resume-test", "checkpoint_ns": ""}}
                    )
                )
                second.conn.close()


class patch_env:
    def __init__(self, key, value):
        self.key, self.value, self.previous = key, value, os.environ.get(key)

    def __enter__(self):
        os.environ[self.key] = self.value

    def __exit__(self, *_):
        if self.previous is None:
            os.environ.pop(self.key, None)
        else:
            os.environ[self.key] = self.previous


if __name__ == "__main__":
    unittest.main()
