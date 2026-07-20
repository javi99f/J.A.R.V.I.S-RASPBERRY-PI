from __future__ import annotations

import hashlib
import re
import secrets
import time
from pathlib import Path

from .history import read_diagnostics, read_history
from .settings import BASE_DIR, get_secret, write_runtime_settings


DEFAULT_PASSWORD_SHA256 = (
    "0294388c97bf9af0ffd74b35daf403e0c1d149b08f3f6f52c6bd43800b8de1c6"
)
PERSONALITY_FILE = BASE_DIR / "config" / "personality_style.txt"
DEVELOPER_SESSION_SECONDS = 30 * 60

SUPPORTED_VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)


def configured_voice() -> str:
    requested = get_secret("JARVIS_VOICE", "Charon")
    for voice in SUPPORTED_VOICES:
        if voice.casefold() == requested.casefold():
            return voice
    return "Charon"


def read_personality_style() -> str:
    try:
        return PERSONALITY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_personality_style(style: str) -> None:
    style = str(style or "").strip()
    if len(style) > 2000:
        raise ValueError("La personalidad no puede superar 2000 caracteres.")
    PERSONALITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PERSONALITY_FILE.with_suffix(".tmp")
    temporary.write_text(style + ("\n" if style else ""), encoding="utf-8")
    temporary.replace(PERSONALITY_FILE)


def write_voice(voice: str) -> str:
    selected = next(
        (item for item in SUPPORTED_VOICES if item.casefold() == str(voice).casefold()),
        None,
    )
    if selected is None:
        raise ValueError("La voz indicada no pertenece a la lista compatible.")
    write_runtime_settings({"JARVIS_VOICE": selected})
    return selected


def _redact(text: str) -> str:
    text = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[API_KEY_REDACTED]", text)
    text = re.sub(
        r"(?i)(api[_ -]?key|token|password)(\s*[:=]\s*)\S+",
        r"\1\2[REDACTED]",
        text,
    )
    return text


def diagnostic_snapshot(limit: int = 8000) -> str:
    errors = _redact(read_diagnostics())[-5000:]
    history = _redact(read_history())[-3000:]
    return (
        "[RECENT RUNTIME ERRORS]\n"
        + (errors or "No recent runtime errors were recorded.")
        + "\n\n[RECENT INTERACTION HISTORY]\n"
        + (history or "No recent interaction history was recorded.")
    )[-max(1000, int(limit)):]


class DeveloperMode:
    """Short-lived local authorization for approved sensitive operations."""

    def __init__(self) -> None:
        self._active_until = 0.0
        self._failed_attempts = 0
        self._locked_until = 0.0

    @property
    def active(self) -> bool:
        return time.monotonic() < self._active_until

    @property
    def remaining_seconds(self) -> int:
        return max(0, int(self._active_until - time.monotonic()))

    def verify(self, password: str) -> tuple[bool, str]:
        now = time.monotonic()
        if now < self._locked_until:
            return False, f"Acceso bloqueado durante {int(self._locked_until - now) + 1} segundos."
        supplied = hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()
        expected = get_secret("DEVELOPER_PASSWORD_SHA256", DEFAULT_PASSWORD_SHA256)
        if secrets.compare_digest(supplied, expected):
            self._failed_attempts = 0
            self._active_until = now + DEVELOPER_SESSION_SECONDS
            return True, "Modo desarrollador activado durante 30 minutos."
        self._failed_attempts += 1
        if self._failed_attempts >= 3:
            self._failed_attempts = 0
            self._locked_until = now + 60
            return False, "Contraseña incorrecta. Acceso bloqueado durante 60 segundos."
        return False, "Contraseña incorrecta."

    def deactivate(self) -> None:
        self._active_until = 0.0
