import unittest

from app.api import health


class AppEntryTests(unittest.TestCase):
    def test_api_health(self):
        response = health()
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.service, "ai-workspace")


if __name__ == "__main__":
    unittest.main()
