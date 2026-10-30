import tempfile
import unittest
from pathlib import Path

from domain.locks import LockTimeoutError, WorkflowLockStore
from domain.object_store import ObjectStore


class LocksAndObjectStoreTests(unittest.TestCase):
    def test_sqlite_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowLockStore(Path(directory) / "locks.db")
            with store.acquire("workflow-1", "owner-1"):
                with self.assertRaises(LockTimeoutError):
                    with store.acquire("workflow-1", "owner-2"):
                        pass

    def test_object_store_copies_and_validates_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "objects"
            source = Path(directory) / "document.pdf"
            source.write_bytes(b"pdf")
            store = ObjectStore(root)
            result = store.put(source, "workflow-1/document.pdf")
            self.assertEqual(store.get_path(result["object_key"]).read_bytes(), b"pdf")
            with self.assertRaises(ValueError):
                store.put(source, "../outside.pdf")
