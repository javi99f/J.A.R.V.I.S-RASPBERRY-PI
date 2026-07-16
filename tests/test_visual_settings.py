import tempfile
import unittest
from pathlib import Path

from omar_ai_core.display.visual_config import (
    MIN_ASSISTANT_SIZE,
    VisualSettings,
    load_visual_settings,
    save_visual_settings,
)


class VisualSettingsTests(unittest.TestCase):
    def test_size_and_visibility_support_the_new_lower_limits(self):
        settings = VisualSettings(assistant_size=48, visibility=0.05).validate()
        self.assertEqual(settings.assistant_size, MIN_ASSISTANT_SIZE)
        self.assertEqual(settings.visibility, 0.2)

    def test_size_and_visibility_are_persistent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "visual.json"
            save_visual_settings(
                VisualSettings(assistant_size=128, visibility=0.42), path
            )
            loaded = load_visual_settings(path)
            self.assertEqual(loaded.assistant_size, 128)
            self.assertAlmostEqual(loaded.visibility, 0.42)


if __name__ == "__main__":
    unittest.main()
