from __future__ import annotations

import math
import os
import threading
import time
from array import array
from collections import deque

import numpy as np


class WakeWordGate:
    """Privacy gate between the microphone and the cloud session.

    In ``wakeword`` mode audio remains local until openWakeWord detects the
    configured phrase. ``continuous`` mode exists only as an explicit fallback
    for testing. ``manual`` never opens automatically.
    """

    FRAME_SAMPLES = 1280  # 80 ms of mono PCM at 16 kHz

    def __init__(
        self,
        mode: str = "wakeword",
        threshold: float = 0.40,
        conversation_seconds: float = 12.0,
        voice_rms_threshold: int = 300,
        confirmation_frames: int = 2,
        vad_threshold: float = 0.0,
        auto_gain: bool = True,
    ) -> None:
        self.mode = mode if mode in {"wakeword", "continuous", "manual"} else "wakeword"
        self.threshold = max(0.05, min(0.99, float(threshold)))
        self.conversation_seconds = max(3.0, float(conversation_seconds))
        self.voice_rms_threshold = max(20, int(voice_rms_threshold))
        self.confirmation_frames = max(1, min(4, int(confirmation_frames)))
        self.vad_threshold = max(0.0, min(0.99, float(vad_threshold)))
        self.auto_gain = bool(auto_gain)
        self._active_until = float("inf") if self.mode == "continuous" else 0.0
        self._fixed_window = False
        self._consecutive_hits = 0
        self._recent_hits = deque(maxlen=10)
        self._model = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self.error = ""
        self.model_name = ""
        self.last_score = 0.0
        self.peak_score = 0.0
        self.last_rms = 0.0
        self.last_peak = 0
        self.frames_processed = 0
        self.prediction_failures = 0
        self.last_detection_at = 0.0
        if self.mode == "wakeword":
            self._load_model()

    def _load_model(self) -> None:
        try:
            import openwakeword
            from openwakeword.model import Model

            candidates = [
                path for path in openwakeword.get_pretrained_model_paths("onnx")
                if "hey_jarvis" in os.path.basename(path)
            ]
            model_path = next((path for path in candidates if os.path.isfile(path)), "")
            if not model_path:
                raise FileNotFoundError(
                    "Falta el modelo local hey_jarvis_v0.1.onnx. "
                    "Ejecuta install_pi4.sh para descargarlo."
                )
            self._model = Model(
                wakeword_models=[model_path],
                inference_framework="onnx",
                # Quiet USB microphones can be rejected by Silero before the
                # actual hey_jarvis model sees their audio, so VAD is opt-in.
                vad_threshold=self.vad_threshold,
            )
            loaded = list(getattr(self._model, "models", {}).keys())
            self.model_name = loaded[0] if loaded else os.path.basename(model_path)
            self.error = ""
        except Exception as exc:
            self.error = f"No se pudo cargar hey_jarvis (ONNX): {exc}"
            self._model = None

    @property
    def available(self) -> bool:
        return self.mode == "continuous" or self._model is not None

    @property
    def active(self) -> bool:
        if self.mode == "continuous":
            return True
        return time.monotonic() < self._active_until

    def activate(self) -> None:
        self._fixed_window = False
        self._active_until = time.monotonic() + self.conversation_seconds

    def activate_for(self, seconds: float) -> None:
        """Open the privacy gate for an explicit short follow-up window."""
        if self.mode != "continuous":
            self._fixed_window = True
            self._active_until = time.monotonic() + max(0.5, float(seconds))

    def extend_conversation(self) -> None:
        # The contextual follow-up window is deliberately exact. Ambient
        # speech must not turn five seconds into a new twelve-second session.
        if self.active and not self._fixed_window:
            self.activate()

    def deactivate(self) -> None:
        if self.mode != "continuous":
            self._active_until = 0.0
            self._fixed_window = False
            self._recent_hits.clear()

    def contains_voice(self, pcm: bytes) -> bool:
        samples = array("h")
        samples.frombytes(pcm)
        if not samples:
            return False
        rms = math.sqrt(sum(int(v) * int(v) for v in samples) / len(samples))
        return rms >= self.voice_rms_threshold

    def health_snapshot(self) -> dict:
        """Return safe wake-word telemetry for settings and diagnostics."""
        return {
            "available": self.available,
            "mode": self.mode,
            "model": self.model_name or "no cargado",
            "threshold": round(self.threshold, 3),
            "vad_threshold": round(self.vad_threshold, 3),
            "auto_gain": self.auto_gain,
            "last_score": round(self.last_score, 4),
            "peak_score": round(self.peak_score, 4),
            "last_rms": round(self.last_rms, 1),
            "last_peak": self.last_peak,
            "frames_processed": self.frames_processed,
            "prediction_failures": self.prediction_failures,
            "last_detection_at": self.last_detection_at,
            "error": self.error,
        }

    def process(self, pcm: bytes) -> tuple[bool, float]:
        """Return ``(detected, score)`` after processing local PCM audio."""
        if self.mode != "wakeword" or self._model is None:
            return False, 0.0

        frame_bytes = self.FRAME_SAMPLES * 2
        best = 0.0
        detected = False
        with self._lock:
            self._buffer.extend(pcm)
            while len(self._buffer) >= frame_bytes:
                raw = bytes(self._buffer[:frame_bytes])
                del self._buffer[:frame_bytes]
                samples = np.frombuffer(raw, dtype=np.int16)
                self.last_rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
                self.last_peak = int(np.max(np.abs(samples.astype(np.int32))))
                model_samples = samples
                # Normalize only the local wake detector. Gemini continues to
                # receive the original PCM, avoiding distortion in conversation.
                if self.auto_gain and 60.0 <= self.last_rms < 1800.0:
                    gain = min(6.0, 2000.0 / max(1.0, self.last_rms))
                    model_samples = np.clip(
                        np.rint(samples.astype(np.float32) * gain), -32768, 32767
                    ).astype(np.int16)
                try:
                    prediction = self._model.predict(model_samples)
                except Exception as exc:
                    self.prediction_failures += 1
                    self.error = f"Fallo ejecutando hey_jarvis: {exc}"
                    self._consecutive_hits = 0
                    return False, best
                self.frames_processed += 1
                frame_best = 0.0
                for value in prediction.values():
                    try:
                        score = float(value[-1] if hasattr(value, "__len__") else value)
                    except (TypeError, ValueError, IndexError):
                        continue
                    frame_best = max(frame_best, score)
                best = max(best, frame_best)
                self.last_score = frame_best
                self.peak_score = max(self.peak_score, frame_best)
                if frame_best >= self.threshold:
                    self._consecutive_hits += 1
                else:
                    self._consecutive_hits = 0
                self._recent_hits.append(frame_best >= self.threshold)
                strong_hit = frame_best >= min(0.99, self.threshold + 0.20)
                confirmed_in_phrase = sum(self._recent_hits) >= self.confirmation_frames
                if strong_hit or confirmed_in_phrase:
                    detected = True
                    self._consecutive_hits = 0
                    self._recent_hits.clear()
                    self.activate()
                    self.last_detection_at = time.time()
                    break
        return detected, best
