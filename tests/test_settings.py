import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omar_ai_core import settings


class SettingsTests(unittest.TestCase):
    def test_gemini_is_the_only_required_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "OPENROUTER_API_KEY": ""}, clear=False):
            self.assertTrue(settings.is_configured())

    def test_example_placeholder_is_not_configured(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "your-gemini-api-key"}, clear=False):
            self.assertFalse(settings.is_configured())

    def test_env_parser_preserves_values_with_equals(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("TOKEN=left=right\n", encoding="utf-8")
            self.assertEqual(settings._parse_env_file(path)["TOKEN"], "left=right")

    def test_live_transport_environment_overrides_are_supported(self):
        with patch.dict(
            os.environ,
            {"LIVE_IP_MODE": "ipv4-first", "LIVE_USE_SYSTEM_PROXY": "0"},
            clear=False,
        ):
            self.assertEqual(settings.get_secret("LIVE_IP_MODE"), "ipv4-first")
            self.assertEqual(settings.get_secret("LIVE_USE_SYSTEM_PROXY"), "0")


if __name__ == "__main__":
    unittest.main()
