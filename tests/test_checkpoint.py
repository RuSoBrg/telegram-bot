import os
import unittest

import psycopg

from domain.checkpoint import create_checkpoint_saver


class CheckpointTests(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("WORKFLOW_DB_PATH", "").startswith(("postgresql://", "postgres://")),
        "requires a PostgreSQL test database",
    )
    def test_checkpoint_saver_uses_postgres(self):
        try:
            saver = create_checkpoint_saver()
        except psycopg.OperationalError as error:
            self.skipTest(f"PostgreSQL de teste indisponível: {error}")
        self.assertEqual(saver.__class__.__name__, "PostgresSaver")


if __name__ == "__main__":
    unittest.main()
