import time
import unittest
from unittest.mock import patch

from omar_ai_core.audio.wakeword import WakeWordGate


class WakeWordGateTests(unittest.TestCase):
    def test_explicit_five_second_followup_window(self):
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=100.0):
            gate = WakeWordGate(mode="manual")
            gate.activate_for(5)
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=104.9):
            self.assertTrue(gate.active)
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=105.1):
            self.assertFalse(gate.active)

    def test_continuous_mode_is_always_active(self):
        gate = WakeWordGate(mode="continuous")
        self.assertTrue(gate.available)
        self.assertTrue(gate.active)
        gate.deactivate()
        self.assertTrue(gate.active)

    def test_manual_mode_is_privacy_safe(self):
        gate = WakeWordGate(mode="manual", conversation_seconds=3)
        self.assertFalse(gate.active)
        detected, score = gate.process(b"\0" * 2048)
        self.assertFalse(detected)
        self.assertEqual(score, 0.0)
        gate.activate()
        self.assertTrue(gate.active)
        gate.deactivate()
        self.assertFalse(gate.active)

    def test_rms_voice_threshold(self):
        gate = WakeWordGate(mode="manual", voice_rms_threshold=300)
        self.assertFalse(gate.contains_voice(b"\0" * 2048))
        loud = (1000).to_bytes(2, "little", signed=True) * 1024
        self.assertTrue(gate.contains_voice(loud))


if __name__ == "__main__":
    unittest.main()
