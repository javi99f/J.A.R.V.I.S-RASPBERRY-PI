import unittest

import numpy as np

from omar_ai_core.display.assistant_state import AssistantState, normalize_state
from omar_ai_core.display.audio_reactive import AudioReactiveAnalyzer


def pcm_tone(frequency: float, amplitude: float, sample_rate: int = 16000) -> bytes:
    timeline = np.arange(2048, dtype=np.float32) / float(sample_rate)
    signal = np.sin(2.0 * np.pi * frequency * timeline) * amplitude
    return (signal * 32767.0).astype("<i2").tobytes()


class AssistantStateTests(unittest.TestCase):
    def test_legacy_states_are_normalized(self):
        self.assertIs(normalize_state("STANDBY"), AssistantState.IDLE)
        self.assertIs(normalize_state("PROCESSING"), AssistantState.THINKING)
        self.assertIs(normalize_state("MUTED"), AssistantState.DISABLED)


class AudioReactiveAnalyzerTests(unittest.TestCase):
    def test_noise_gate_ignores_quiet_background(self):
        analyzer = AudioReactiveAnalyzer(noise_gate=0.01)
        analyzer.feed_input(pcm_tone(440.0, 0.002), 16000)
        features = analyzer.snapshot("LISTENING")
        self.assertEqual(features.current_amplitude, 0.0)
        self.assertFalse(features.is_voice_active)

    def test_input_pcm_drives_listening_and_is_normalized(self):
        analyzer = AudioReactiveAnalyzer()
        analyzer.feed_input(pcm_tone(180.0, 0.45), 16000)
        features = analyzer.snapshot("LISTENING")
        self.assertGreater(features.smoothed_amplitude, 0.1)
        self.assertGreater(features.low_frequency_energy, features.high_frequency_energy)
        for value in features.as_dict().values():
            if isinstance(value, bool):
                continue
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_speaking_uses_output_pcm_not_microphone_pcm(self):
        analyzer = AudioReactiveAnalyzer()
        analyzer.feed_input(pcm_tone(180.0, 0.01), 16000)
        analyzer.feed_output(pcm_tone(1200.0, 0.45), 24000)
        listening = analyzer.snapshot("LISTENING")
        speaking = analyzer.snapshot("SPEAKING")
        self.assertGreater(speaking.smoothed_amplitude, listening.smoothed_amplitude)
        self.assertGreater(speaking.mid_frequency_energy, speaking.low_frequency_energy)

    def test_non_audio_states_suppress_pcm_activity(self):
        analyzer = AudioReactiveAnalyzer()
        analyzer.feed_input(pcm_tone(440.0, 0.45), 16000)
        listening = analyzer.snapshot("LISTENING")
        thinking = analyzer.snapshot("THINKING")
        self.assertLess(thinking.smoothed_amplitude, listening.smoothed_amplitude * 0.1)
        self.assertFalse(thinking.is_voice_active)


if __name__ == "__main__":
    unittest.main()
