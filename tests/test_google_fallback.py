import unittest
from unittest.mock import Mock

from graph import _content_to_text, _invoke_google


class GoogleFallbackTests(unittest.TestCase):
    def test_uses_fallback_for_service_unavailable(self):
        primary = Mock()
        primary.invoke.side_effect = RuntimeError("503 UNAVAILABLE")
        fallback = Mock()
        fallback.invoke.return_value = "fallback response"

        result = _invoke_google(primary, fallback, ["message"])

        self.assertEqual(result, "fallback response")
        fallback.invoke.assert_called_once_with(["message"])

    def test_does_not_hide_authentication_errors(self):
        primary = Mock()
        primary.invoke.side_effect = RuntimeError("401 UNAUTHENTICATED")
        fallback = Mock()

        with self.assertRaisesRegex(RuntimeError, "401"):
            _invoke_google(primary, fallback, ["message"])

        fallback.invoke.assert_not_called()

    def test_normalizes_structured_text_content(self):
        content = [{"type": "text", "text": "Olá"}, {"type": "text", "text": " mundo"}]

        self.assertEqual(_content_to_text(content), "Olá mundo")


if __name__ == "__main__":
    unittest.main()
