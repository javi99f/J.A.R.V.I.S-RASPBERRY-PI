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

    def test_followup_window_cannot_be_extended_by_ambient_voice(self):
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=100.0):
            gate = WakeWordGate(mode="manual")
            gate.activate_for(5)
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=103.0):
            gate.extend_conversation()
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=105.1):
            self.assertFalse(gate.active)

    def test_normal_conversation_can_still_be_extended(self):
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=100.0):
            gate = WakeWordGate(mode="manual", conversation_seconds=12)
            gate.activate()
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=110.0):
            gate.extend_conversation()
        with patch("omar_ai_core.audio.wakeword.time.monotonic", return_value=121.9):
            self.assertTrue(gate.active)

    def test_wakeword_requires_confirmation_unless_score_is_strong(self):
        class FakeModel:
            def __init__(self, scores):
                self.scores = iter(scores)

            def predict(self, _samples):
                return {"hey jarvis": [next(self.scores)]}

        frame = b"\0" * (WakeWordGate.FRAME_SAMPLES * 2)
        gate = WakeWordGate(mode="manual", threshold=0.55, confirmation_frames=2)
        gate.mode = "wakeword"
        gate._model = FakeModel([0.60, 0.61])
        self.assertFalse(gate.process(frame)[0])
        self.assertTrue(gate.process(frame)[0])

        strong = WakeWordGate(mode="manual", threshold=0.55, confirmation_frames=2)
        strong.mode = "wakeword"
        strong._model = FakeModel([0.80])
        self.assertTrue(strong.process(frame)[0])

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
