import unittest

from omar_ai_core.display.visual_config import (
    MAX_VISIBILITY,
    MIN_ASSISTANT_SIZE,
    VisualSettings,
    estimated_core_diameter,
)


class VisualSettingsTests(unittest.TestCase):
    def test_visibility_can_reach_double_strength(self):
        settings = VisualSettings(visibility=3.0).validate()
        self.assertEqual(settings.visibility, MAX_VISIBILITY)
        self.assertEqual(MAX_VISIBILITY, 2.0)

    def test_minimum_size_matches_reference_capture(self):
        settings = VisualSettings(assistant_size=1).validate()
        self.assertEqual(settings.assistant_size, MIN_ASSISTANT_SIZE)
        self.assertEqual(estimated_core_diameter(MIN_ASSISTANT_SIZE), 170)

    def test_orbital_nodes_are_always_enabled(self):
        settings = VisualSettings(droplets=False).validate()
        self.assertTrue(settings.droplets)


if __name__ == "__main__":
    unittest.main()
