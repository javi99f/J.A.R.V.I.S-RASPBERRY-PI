from __future__ import annotations

import ctypes
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from omar_ai_core.settings import BASE_DIR


CONFIG_PATH = BASE_DIR / "config" / "liquid_visual.json"
MIN_ASSISTANT_SIZE = 109
MAX_ASSISTANT_SIZE = 420
RENDERER_PADDING = 72
CORE_DIAMETER_RATIO = 0.94
MIN_VISIBILITY = 0.2
MAX_VISIBILITY = 2.0


def estimated_core_diameter(assistant_size: int) -> int:
    """Approximate visible hologram diameter in logical screen pixels."""
    return round((int(assistant_size) + RENDERER_PADDING) * CORE_DIAMETER_RATIO)


def _system_reduced_motion() -> bool:
    explicit = os.getenv("JARVIS_REDUCED_MOTION", "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    if sys.platform != "win32":
        return False
    enabled = ctypes.c_int(1)
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return bool(ok) and not bool(enabled.value)
    except Exception:
        return False


@dataclass(slots=True)
class VisualSettings:
    motion_intensity: float = 1.0
    microphone_sensitivity: float = 1.0
    visibility: float = 1.0
    assistant_size: int = 360
    quality: str = "balanced"
    droplets: bool = True
    reduced_motion: bool = False

    @property
    def raymarch_steps(self) -> int:
        return {"economy": 28, "balanced": 34, "high": 46}.get(self.quality, 34)

    def validate(self) -> "VisualSettings":
        self.motion_intensity = max(0.25, min(1.6, float(self.motion_intensity)))
        self.microphone_sensitivity = max(
            0.4, min(2.5, float(self.microphone_sensitivity))
        )
        self.visibility = max(MIN_VISIBILITY, min(MAX_VISIBILITY, float(self.visibility)))
        self.assistant_size = max(
            MIN_ASSISTANT_SIZE, min(MAX_ASSISTANT_SIZE, int(self.assistant_size))
        )
        if self.quality not in {"economy", "balanced", "high"}:
            self.quality = "balanced"
        # Orbital nodes are part of the core design and are always enabled.
        self.droplets = True
        self.reduced_motion = bool(self.reduced_motion)
        return self


def load_visual_settings(path: Path = CONFIG_PATH) -> VisualSettings:
    defaults = VisualSettings(reduced_motion=_system_reduced_motion())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults
    for key in asdict(defaults):
        if key in raw:
            setattr(defaults, key, raw[key])
    return defaults.validate()


def save_visual_settings(settings: VisualSettings, path: Path = CONFIG_PATH) -> None:
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
