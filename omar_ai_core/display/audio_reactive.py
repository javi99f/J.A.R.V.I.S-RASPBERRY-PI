from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace

import numpy as np

from .assistant_state import AssistantState, normalize_state


@dataclass(slots=True)
class AudioFeatures:
    current_amplitude: float = 0.0
    smoothed_amplitude: float = 0.0
    low_frequency_energy: float = 0.0
    mid_frequency_energy: float = 0.0
    high_frequency_energy: float = 0.0
    is_voice_active: bool = False
    peak_impulse: float = 0.0
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "currentAmplitude": self.current_amplitude,
            "smoothedAmplitude": self.smoothed_amplitude,
            "lowFrequencyEnergy": self.low_frequency_energy,
            "midFrequencyEnergy": self.mid_frequency_energy,
            "highFrequencyEnergy": self.high_frequency_energy,
            "isVoiceActive": self.is_voice_active,
            "peakImpulse": self.peak_impulse,
        }


class AudioReactiveAnalyzer:
    """Thread-safe PCM analyser fed by Jarvis' existing audio streams.

    It never opens an input or output device. Runtime feeds the exact PCM blocks
    already used by recognition and playback, so there is no duplicate capture
    and no possibility of stealing the microphone from sounddevice.
    """

    def __init__(self, sensitivity: float = 1.0, noise_gate: float = 0.0085):
        self.sensitivity = max(0.2, min(3.0, float(sensitivity)))
        self.noise_gate = max(0.0, min(0.08, float(noise_gate)))
        self._lock = threading.Lock()
        self._input = AudioFeatures()
        self._output = AudioFeatures()
        self._windows: dict[int, np.ndarray] = {}

    def set_sensitivity(self, value: float) -> None:
        with self._lock:
            self.sensitivity = max(0.2, min(3.0, float(value)))

    def feed_input(self, pcm: bytes, sample_rate: int) -> None:
        self._feed("input", pcm, sample_rate)

    def feed_output(self, pcm: bytes, sample_rate: int) -> None:
        self._feed("output", pcm, sample_rate)

    def _feed(self, source: str, pcm: bytes, sample_rate: int) -> None:
        if not pcm or sample_rate <= 0:
            return
        samples_i16 = np.frombuffer(pcm, dtype="<i2")
        if samples_i16.size < 32:
            return
        samples = samples_i16.astype(np.float32) * (1.0 / 32768.0)
        rms = float(np.sqrt(np.mean(samples * samples) + 1e-12))

        with self._lock:
            target = self._input if source == "input" else self._output
            sensitivity = self.sensitivity
            gate = self.noise_gate

        amplitude = float(np.clip((rms - gate) * 18.0 * sensitivity, 0.0, 1.0))
        coefficient = 0.72 if amplitude > target.smoothed_amplitude else 0.16
        smoothed = target.smoothed_amplitude + coefficient * (
            amplitude - target.smoothed_amplitude
        )

        window = self._windows.get(samples.size)
        if window is None:
            window = np.hanning(samples.size).astype(np.float32)
            self._windows[samples.size] = window
        spectrum = np.abs(np.fft.rfft(samples * window)) ** 2
        frequencies = np.fft.rfftfreq(samples.size, 1.0 / float(sample_rate))
        total = float(np.sum(spectrum)) + 1e-12

        def band_energy(low: float, high: float) -> float:
            mask = (frequencies >= low) & (frequencies < high)
            fraction = float(np.sum(spectrum[mask]) / total) if np.any(mask) else 0.0
            return float(np.clip(smoothed * (0.55 + 1.9 * math.sqrt(fraction)), 0.0, 1.0))

        low_energy = band_energy(45.0, 280.0)
        mid_energy = band_energy(280.0, 2400.0)
        high_energy = band_energy(2400.0, min(7600.0, sample_rate * 0.49))
        transient = max(0.0, amplitude - target.smoothed_amplitude)
        peak = max(target.peak_impulse * 0.72, min(1.0, transient * 3.2))
        updated = AudioFeatures(
            current_amplitude=amplitude,
            smoothed_amplitude=smoothed,
            low_frequency_energy=low_energy,
            mid_frequency_energy=mid_energy,
            high_frequency_energy=high_energy,
            is_voice_active=smoothed >= 0.045,
            peak_impulse=peak,
            updated_at=time.monotonic(),
        )
        with self._lock:
            if source == "input":
                self._input = updated
            else:
                self._output = updated

    @staticmethod
    def _decayed(features: AudioFeatures, now: float) -> AudioFeatures:
        age = max(0.0, now - features.updated_at)
        if age <= 0.12:
            return replace(features)
        decay = math.exp(-(age - 0.12) * 5.8)
        return AudioFeatures(
            current_amplitude=features.current_amplitude * decay,
            smoothed_amplitude=features.smoothed_amplitude * decay,
            low_frequency_energy=features.low_frequency_energy * decay,
            mid_frequency_energy=features.mid_frequency_energy * decay,
            high_frequency_energy=features.high_frequency_energy * decay,
            is_voice_active=features.is_voice_active and decay > 0.35,
            peak_impulse=features.peak_impulse * math.exp(-(age - 0.12) * 8.0),
            updated_at=features.updated_at,
        )

    def snapshot(self, state: str | AssistantState) -> AudioFeatures:
        selected = normalize_state(state)
        with self._lock:
            source = self._output if selected is AssistantState.SPEAKING else self._input
            copied = replace(source)
        features = self._decayed(copied, time.monotonic())
        if selected not in {AssistantState.LISTENING, AssistantState.SPEAKING}:
            features.current_amplitude *= 0.08
            features.smoothed_amplitude *= 0.08
            features.low_frequency_energy *= 0.08
            features.mid_frequency_energy *= 0.08
            features.high_frequency_energy *= 0.08
            features.peak_impulse *= 0.08
            features.is_voice_active = False
        return features

    def reset(self) -> None:
        with self._lock:
            self._input = AudioFeatures()
            self._output = AudioFeatures()
