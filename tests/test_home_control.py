import unittest
from unittest.mock import patch

from omar_ai_core.tools import home_control as home


DEVICES = [
    {"entity_id": "light.desk_left", "state": "off", "attributes": {"friendly_name": "Desk light left"}},
    {"entity_id": "light.desk_right", "state": "off", "attributes": {"friendly_name": "Desk light right"}},
]


class HomeControlSafetyTests(unittest.TestCase):
    @patch.object(home, "_states", return_value=DEVICES)
    @patch.object(home, "_call_service")
    def test_ambiguous_target_does_not_change_devices(self, call_service, _states):
        result = home.home_control({"action": "turn_on", "target": "desk light"})
        self.assertIn("several devices", result)
        call_service.assert_not_called()

    @patch.object(home, "_states", return_value=DEVICES)
    @patch.object(home, "_call_service")
    def test_explicit_target_changes_one_device(self, call_service, _states):
        result = home.home_control({"action": "turn_on", "target": "desk light left"})
        self.assertIn("turned on", result)
        call_service.assert_called_once()


if __name__ == "__main__":
    unittest.main()

