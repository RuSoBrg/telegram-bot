import tempfile
import unittest
from pathlib import Path

from domain.approval import requires_human_approval
from domain.quotas import QuotaExceededError, UserQuotaStore


class QuotasAndApprovalTests(unittest.TestCase):
    def test_quota_blocks_after_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserQuotaStore(Path(directory) / "quota.db", max_operations=1)
            store.consume("user", "workflow")
            with self.assertRaises(QuotaExceededError):
                store.consume("user", "workflow")

    def test_media_requires_approval(self):
        request = requires_human_approval(
            target="marketing",
            artifacts=[{"mime_type": "video/mp4"}],
            sources=[],
            objective="Criar vídeo",
        )
        self.assertIsNotNone(request)
        self.assertEqual(request.action, "gerar e enviar para Telegram")
