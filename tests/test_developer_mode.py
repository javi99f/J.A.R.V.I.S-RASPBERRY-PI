import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omar_ai_core import developer


class DeveloperModeTests(unittest.TestCase):
    def test_default_password_is_checked_locally_from_a_hash(self):
        mode = developer.DeveloperMode()
        password = "clave-de-prueba-local"
        expected = hashlib.sha256(password.encode("utf-8")).hexdigest()
        with patch.object(developer, "get_secret", return_value=expected):
            allowed, _ = mode.verify(password)
        self.assertTrue(allowed)
        self.assertTrue(mode.active)
        self.assertNotIn(password, Path(developer.__file__).read_text(encoding="utf-8"))

    def test_wrong_password_does_not_unlock(self):
        mode = developer.DeveloperMode()
        allowed, _ = mode.verify("incorrecta")
        self.assertFalse(allowed)
        self.assertFalse(mode.active)

    def test_personality_is_stored_separately_from_core_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personality_style.txt"
            with patch.object(developer, "PERSONALITY_FILE", target):
                developer.write_personality_style("Habla de forma calmada.")
                self.assertEqual(developer.read_personality_style(), "Habla de forma calmada.")

    def test_voice_is_validated_before_saving(self):
        with patch.object(developer, "write_runtime_settings") as writer:
            selected = developer.write_voice("sulafat")
        self.assertEqual(selected, "Sulafat")
        writer.assert_called_once_with({"JARVIS_VOICE": "Sulafat"})
        with self.assertRaises(ValueError):
            developer.write_voice("voz-inexistente")

    def test_diagnostics_redact_api_keys(self):
        with (
            patch.object(developer, "read_diagnostics", return_value="API_KEY=AIzaabcdefghijklmnopqrstuvwxyz123456"),
            patch.object(developer, "read_history", return_value="todo bien"),
        ):
            snapshot = developer.diagnostic_snapshot()
        self.assertNotIn("AIzaabcdefghijklmnopqrstuvwxyz123456", snapshot)
        self.assertIn("REDACTED", snapshot)


if __name__ == "__main__":
    unittest.main()
