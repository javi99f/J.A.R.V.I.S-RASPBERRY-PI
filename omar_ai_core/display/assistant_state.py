from __future__ import annotations

from enum import Enum


class AssistantState(str, Enum):
    """Single visual state vocabulary used by the desktop interface."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"
    DISABLED = "disabled"


_ALIASES = {
    "IDLE": AssistantState.IDLE,
    "STANDBY": AssistantState.IDLE,
    "READY": AssistantState.IDLE,
    "LISTENING": AssistantState.LISTENING,
    "THINKING": AssistantState.THINKING,
    "PROCESSING": AssistantState.THINKING,
    "SPEAKING": AssistantState.SPEAKING,
    "ERROR": AssistantState.ERROR,
    "FAILED": AssistantState.ERROR,
    "MUTED": AssistantState.DISABLED,
    "DISABLED": AssistantState.DISABLED,
}


def normalize_state(value: str | AssistantState) -> AssistantState:
    if isinstance(value, AssistantState):
        return value
    return _ALIASES.get(str(value or "").strip().upper(), AssistantState.IDLE)


STATE_LABELS = {
    AssistantState.IDLE: "EN REPOSO",
    AssistantState.LISTENING: "ESCUCHANDO",
    AssistantState.THINKING: "PENSANDO",
    AssistantState.SPEAKING: "HABLANDO",
    AssistantState.ERROR: "ERROR DE CONEXIÓN",
    AssistantState.DISABLED: "MICRÓFONO DESACTIVADO",
}


def state_label(value: str | AssistantState) -> str:
    return STATE_LABELS[normalize_state(value)]
