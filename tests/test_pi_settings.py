import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from omar_ai_core import history, runtime, settings
from omar_ai_core.display.hud import enumerate_pi_audio_devices


class PiSettingsTests(unittest.TestCase):
    def test_audio_devices_are_filtered_by_direction(self):
        devices = [
            {"name": "USB Microphone", "max_input_channels": 1, "max_output_channels": 0},
            {"name": "USB Speaker", "max_input_channels": 0, "max_output_channels": 2},
            {"name": "Disabled", "max_input_channels": 0, "max_output_channels": 0},
        ]
        self.assertEqual(
            enumerate_pi_audio_devices("input", devices),
            [("0: USB Microphone", 0)],
        )
        self.assertEqual(
            enumerate_pi_audio_devices("output", devices),
            [("1: USB Speaker", 1)],
        )

    def test_audio_selection_preserves_existing_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / ".env"
            env_file.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
            with patch.object(settings, "ENV_FILE", env_file):
                settings.write_audio_devices(3, 0)
                saved = settings._parse_env_file(env_file)
            self.assertEqual(saved["GEMINI_API_KEY"], "test-key")
            self.assertEqual(saved["INPUT_DEVICE"], "3")
            self.assertEqual(saved["OUTPUT_DEVICE"], "0")

    def test_history_is_saved_and_read_back(self):
        with tempfile.TemporaryDirectory() as folder:
            history_file = Path(folder) / "jarvis-history.log"
            with patch.object(history, "HISTORY_FILE", history_file):
                history.append_history("You: hola")
                history.append_history("Jarvis: Buenos días")
                saved = history.read_history()
            self.assertIn("You: hola", saved)
            self.assertIn("Jarvis: Buenos días", saved)

    def test_pcm_is_resampled_for_common_usb_audio_rates(self):
        source = np.arange(160, dtype="<i2").tobytes()
        converted = runtime._convert_pcm16(source, 16000, 48000, 1, 2)
        samples = np.frombuffer(converted, dtype="<i2").reshape(-1, 2)
        self.assertEqual(samples.shape, (480, 2))
        np.testing.assert_array_equal(samples[:, 0], samples[:, 1])


if __name__ == "__main__":
    unittest.main()
