import unittest
from types import SimpleNamespace
from unittest.mock import patch

from omar_ai_core.runtime import JarvisLive


class _UI:
    muted = True
    on_text_command = None
    on_manual_activate = None


class StartupListeningTests(unittest.TestCase):
    def test_each_process_start_clears_previous_listening_mute(self):
        wake_gate = SimpleNamespace(
            mode="wakeword",
            available=True,
            error=None,
        )
        ui = _UI()
        with patch("omar_ai_core.runtime.UpdateManager"), patch(
            "omar_ai_core.runtime.DeveloperMode"
        ), patch(
            "omar_ai_core.runtime.WakeWordGate", return_value=wake_gate
        ), patch(
            "omar_ai_core.runtime.listening_state.set_listening_muted"
        ) as set_muted:
            JarvisLive(ui)

        set_muted.assert_called_once_with(False)
        self.assertFalse(ui.muted)


if __name__ == "__main__":
    unittest.main()
