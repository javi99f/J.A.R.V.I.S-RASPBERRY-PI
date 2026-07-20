from __future__ import annotations

import math
import threading
import time
from array import array


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
        threshold: float = 0.55,
        conversation_seconds: float = 12.0,
        voice_rms_threshold: int = 300,
    ) -> None:
        self.mode = mode if mode in {"wakeword", "continuous", "manual"} else "wakeword"
        self.threshold = max(0.05, min(0.99, float(threshold)))
        self.conversation_seconds = max(3.0, float(conversation_seconds))
        self.voice_rms_threshold = max(20, int(voice_rms_threshold))
        self._active_until = float("inf") if self.mode == "continuous" else 0.0
        self._model = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self.error = ""
        if self.mode == "wakeword":
            self._load_model()

    def _load_model(self) -> None:
        try:
            from openwakeword.model import Model

            # The bundled model recognises "hey jarvis". VAD reduces accidental
            # activations caused by fans, television and constant room noise.
            try:
                self._model = Model(
                    wakeword_models=["hey jarvis"],
                    inference_framework="onnx",
                    vad_threshold=0.35,
                )
            except Exception as vad_exc:
                # Some Raspberry Pi Python builds cannot load Silero VAD. Wake
                # detection remains local and usable without that extra filter.
                self.error = f"VAD disabled: {vad_exc}"
                self._model = Model(
                    wakeword_models=["hey jarvis"],
                    inference_framework="onnx",
                    vad_threshold=0,
                )
        except Exception as exc:
            self.error = str(exc)
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
        self._active_until = time.monotonic() + self.conversation_seconds

    def activate_for(self, seconds: float) -> None:
        """Open the privacy gate for an explicit short follow-up window."""
        if self.mode != "continuous":
            self._active_until = time.monotonic() + max(0.5, float(seconds))

    def extend_conversation(self) -> None:
        if self.active:
            self.activate()

    def deactivate(self) -> None:
        if self.mode != "continuous":
            self._active_until = 0.0

    def contains_voice(self, pcm: bytes) -> bool:
        samples = array("h")
        samples.frombytes(pcm)
        if not samples:
            return False
        rms = math.sqrt(sum(int(v) * int(v) for v in samples) / len(samples))
        return rms >= self.voice_rms_threshold

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
                import numpy as np

                samples = np.frombuffer(raw, dtype=np.int16)
                prediction = self._model.predict(samples)
                for value in prediction.values():
                    try:
                        score = float(value[-1] if hasattr(value, "__len__") else value)
                    except (TypeError, ValueError, IndexError):
                        continue
                    best = max(best, score)
                if best >= self.threshold:
                    detected = True
                    self.activate()
                    break
        return detected, best
