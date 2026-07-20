import asyncio
import contextlib
import socket
import os
import re
import threading
import sys
import time
import traceback
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types
from .display.hud import JarvisUI
from .memory.store import (
    load_memory, update_memory, format_memory_for_prompt,
)

from .tools.web_lookup import web_search as web_search_action
from .tools.pi_device import pi_controls
from .tools.home_control import home_control
from .tools.spotify_control import spotify_control, spotify_snapshot
from .diagnostic_export import create_diagnostic_report, share_diagnostic_report
from .settings import BASE_DIR, get_secret, is_desktop_mode, require_secret
from .state import listening as listening_state
from .audio.wakeword import WakeWordGate
from .updater import ReleaseInfo, UpdateManager
from .developer import (
    DeveloperMode,
    append_developer_audit,
    configured_voice,
    diagnostic_snapshot,
    read_developer_audit,
    read_personality_style,
    write_personality_style,
    write_voice,
)


def get_base_dir():
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle / "omar_ai_core"
    return Path(__file__).resolve().parent


PACKAGE_DIR     = get_base_dir()
PROJECT_DIR     = BASE_DIR
PROMPT_PATH     = PACKAGE_DIR / "persona" / "system_prompt.txt"
RUNTIME_LOG_PATH = PROJECT_DIR / "jarvis-runtime.log"
LIVE_MODEL          = "gemini-3.1-flash-live-preview"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

WAKE_PATTERN = re.compile(
    r"\b(jarvis|assistant)\b.*\b(unmute|wake up|listen|start listening|resume listening|despierta|escucha|act[ií]vate)\b"
    r"|\b(unmute|wake up|listen|start listening|resume listening|despierta|escucha|act[ií]vate)\b.*\b(jarvis|assistant)\b",
    re.IGNORECASE,
)

SELF_CHANGE_MEMORY_PATTERN = re.compile(
    r"\b(jarvis|assistant|asistente|error|mistake|fallo|correg|fix|improv|mejor|siri|wake|activaci[oó]n)\w*\b",
    re.IGNORECASE,
)

LISTENING_MUTE_ACTIONS = {"listening_mute", "assistant_mute", "mute_listening", "stop_listening"}
LISTENING_UNMUTE_ACTIONS = {"listening_unmute", "assistant_unmute", "unmute_listening", "start_listening"}
SPEAKER_MUTE_ACTIONS = {"speaker_mute", "mute_speaker", "volume_mute"}
SPEAKER_UNMUTE_ACTIONS = {"speaker_unmute", "unmute_speaker", "volume_unmute"}


def _get_api_key() -> str:
    return require_secret("GEMINI_API_KEY")


def _configured_audio_device(name: str) -> int | None:
    value = get_secret(name)
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _audio_stream_format(
    device: int | None,
    direction: str,
    target_rate: int,
) -> tuple[int, int]:
    """Choose a sample rate and channel count accepted by the selected endpoint."""
    info = sd.query_devices(device, direction)
    channel_key = "max_input_channels" if direction == "input" else "max_output_channels"
    max_channels = max(1, int(info[channel_key]))
    default_rate = int(round(float(info.get("default_samplerate") or target_rate)))
    rates = list(dict.fromkeys((default_rate, target_rate, 48000, 44100)))
    channels = list(dict.fromkeys((1, min(2, max_channels), max_channels)))
    checker = sd.check_input_settings if direction == "input" else sd.check_output_settings
    last_error: Exception | None = None
    for rate in rates:
        for channel_count in channels:
            try:
                checker(
                    device=device,
                    channels=channel_count,
                    dtype="int16",
                    samplerate=rate,
                )
                return rate, channel_count
            except Exception as exc:
                last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"No compatible {direction} audio format")


def _convert_pcm16(
    data: bytes,
    source_rate: int,
    target_rate: int,
    source_channels: int = 1,
    target_channels: int = 1,
) -> bytes:
    """Resample mono PCM and adapt it to the selected device channels."""
    samples = np.frombuffer(data, dtype="<i2")
    if samples.size == 0:
        return b""
    source_channels = max(1, int(source_channels))
    usable = samples.size - (samples.size % source_channels)
    if usable <= 0:
        return b""
    mono = samples[:usable].reshape(-1, source_channels).astype(np.float32).mean(axis=1)
    if source_rate != target_rate and mono.size > 1:
        target_length = max(1, int(round(mono.size * target_rate / source_rate)))
        positions = np.arange(target_length, dtype=np.float32) * (source_rate / target_rate)
        mono = np.interp(positions, np.arange(mono.size, dtype=np.float32), mono)
    converted = np.clip(np.rint(mono), -32768, 32767).astype("<i2")
    if target_channels > 1:
        converted = np.repeat(converted[:, None], target_channels, axis=1).reshape(-1)
    return converted.tobytes()


def _restart_portaudio_backend() -> None:
    sd._terminate()
    sd._initialize()


def _configure_live_transport() -> None:
    """Harden Gemini Live's WebSocket connection on Raspberry Pi networks.

    The GenAI SDK doesn't currently expose WebSocket connection options on
    ``live.connect``.  Its default ten-second opening timeout is too short on
    some Pi/network combinations, and broken IPv6 routes can consume the
    entire timeout before IPv4 is attempted.  websockets 15+ also discovers
    system and environment proxies automatically; a proxy that works for
    ordinary HTTPS may still stall a WebSocket upgrade.  Keep the SDK
    implementation but supply conservative transport defaults before the
    client is created.
    """
    try:
        import google.genai.live as live_module

        original_connect = live_module.ws_connect
        if getattr(original_connect, "_jarvis_transport", False):
            return

        timeout = max(10.0, float(get_secret("LIVE_OPEN_TIMEOUT_SECONDS", "45")))
        ip_mode = get_secret("LIVE_IP_MODE", "").strip().lower()
        if not ip_mode:
            # Backwards compatibility with the first Pi image. "Force" now
            # means prefer IPv4, then fall back to the normal resolver; it no
            # longer removes every non-IPv4 route.
            legacy_force_ipv4 = get_secret("LIVE_FORCE_IPV4", "0").lower()
            ip_mode = (
                "ipv4-first"
                if legacy_force_ipv4 not in {"0", "false", "no", "off", ""}
                else ("auto" if is_desktop_mode() else "ipv4-first")
            )
        if ip_mode not in {
            "auto", "ipv4-first", "ipv6-first", "ipv4-only", "ipv6-only"
        }:
            ip_mode = "auto"
        use_system_proxy = get_secret(
            "LIVE_USE_SYSTEM_PROXY", "0" if not is_desktop_mode() else "1"
        ).lower() not in {"0", "false", "no", "off"}

        @contextlib.asynccontextmanager
        async def jarvis_ws_connect(*args, **kwargs):
            base_kwargs = dict(kwargs)
            base_kwargs.setdefault("open_timeout", timeout)
            if not use_system_proxy:
                # websockets.connect defaults to proxy=True since v15. A Pi
                # appliance on a normal LAN should connect directly; this
                # also guarantees that an address-family preference applies
                # to Google's host rather than to an auto-detected proxy.
                base_kwargs.setdefault("proxy", None)

            if "family" in base_kwargs:
                attempts = [base_kwargs]
            else:
                auto = dict(base_kwargs)
                ipv4 = {**base_kwargs, "family": socket.AF_INET}
                ipv6 = {**base_kwargs, "family": socket.AF_INET6}
                attempts = {
                    "auto": [auto, ipv4],
                    "ipv4-first": [ipv4, auto],
                    "ipv6-first": [ipv6, auto],
                    "ipv4-only": [ipv4],
                    "ipv6-only": [ipv6],
                }[ip_mode]

            websocket = None
            for index, attempt_kwargs in enumerate(attempts):
                try:
                    websocket = await original_connect(*args, **attempt_kwargs)
                    break
                except (TimeoutError, OSError) as exc:
                    if index == len(attempts) - 1:
                        raise
                    print(
                        "[JARVIS] Live handshake route failed "
                        f"({type(exc).__name__}); trying alternate route."
                    )

            if websocket is None:  # pragma: no cover - defensive invariant
                raise RuntimeError("No Gemini Live transport route was attempted.")
            try:
                yield websocket
            finally:
                await websocket.close()

        jarvis_ws_connect._jarvis_transport = True
        live_module.ws_connect = jarvis_ws_connect
    except Exception as exc:
        print(f"[JARVIS] Live transport defaults unavailable: {exc}")


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, a private Raspberry Pi voice assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results - always call the appropriate tool."
        )


def _normalized_action(args: dict) -> str:
    return str(args.get("action") or "").lower().strip().replace("-", "_").replace(" ", "_")


def _is_wake_phrase(text: str) -> bool:
    return bool(WAKE_PATTERN.search(text or ""))
    
TOOL_DECLARATIONS = [
    {
        "name": "web_search",
        "description": (
            "Looks up live public information. Use only for current/latest facts, source links, news, "
            "prices, schedules, or recent public research. Do not use for ordinary conversation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Search query"},
                "mode": {"type": "STRING", "description": "search or compare"},
                "items": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "Comparison aspect"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "social_insights",
        "description": (
            "Answers Instagram, IG, TikTok, and Zernio analytics questions for connected accounts. "
            "Use for followers, engagement, post performance, views, likes, comments, shares, reach, "
            "latest posts, last N posts, and account status."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "question": {"type": "STRING", "description": "The original social analytics question."},
                "platform": {"type": "STRING", "description": "instagram | tiktok | both"},
                "action": {"type": "STRING", "description": "ask | followers | accounts | latest_post | summary | recent_posts"},
                "days": {"type": "INTEGER", "description": "Days of analytics to inspect."},
                "post_count": {"type": "INTEGER", "description": "Number of recent posts requested."},
                "username": {"type": "STRING", "description": "Optional account username."},
            },
            "required": ["question"],
        },
    },
    {
        "name": "pi_controls",
        "description": (
            "Controls this Raspberry Pi assistant appliance only. Use listening_mute/listening_unmute "
            "when the user asks JARVIS to mute itself, stop listening, wake up, or listen again. "
            "Use speaker_mute/speaker_unmute only when the user asks to mute or unmute sound, volume, "
            "audio output, or speakers. Every request about volume controls the Raspberry Pi system volume, "
            "including while Spotify is playing. Also controls a configured Bluetooth speaker and screen brightness. "
            "Do not use for room lights, desktop apps, files, keyboard, mouse, or general computer automation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "listening_mute | listening_unmute | speaker_mute | speaker_unmute | "
                        "volume_set | volume_up | volume_down | brightness_set | brightness_up | "
                        "brightness_down | connect_speaker"
                    ),
                },
                "value": {"type": "STRING", "description": "Percent value for volume/brightness."},
                "description": {"type": "STRING", "description": "Original user command."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "home_control",
        "description": (
            "Controls Home Assistant smart-home lights and switches. Use this for room lights, lamps, "
            "bulbs, LED strips, desk lights, wall panels, monitor backlights, floor lamps, smart plugs, "
            "and Home Assistant entity status. Do not use pi_controls for room lights."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "turn_on | turn_off | toggle | status | list_entities | brightness_set",
                },
                "target": {
                    "type": "STRING",
                    "description": "Device/entity name, e.g. all lights, desk lamp, wall panel, monitor backlight.",
                },
                "domain": {"type": "STRING", "description": "light | switch | any"},
                "brightness": {"type": "INTEGER", "description": "Brightness percent for lights."},
                "description": {"type": "STRING", "description": "Original user command."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "spotify_player",
        "description": (
            "Controls Spotify playback on the Raspberry Pi through its configured Spotify Connect "
            "receiver. Use for natural requests to play a song, artist, album, or playlist; pause, "
            "resume, skip, go back, report the current track, and list or select Spotify devices. "
            "A bare 'pausa', 'continúa', 'anterior', or 'siguiente' refers to Spotify playback. "
            "Never use this tool for volume; all volume requests use pi_controls. "
            "Do not claim music started unless this tool reports success."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "play | pause | resume | next | previous | current | devices | transfer"
                    ),
                },
                "query": {
                    "type": "STRING",
                    "description": "Song, artist, album, playlist, or search phrase requested by the user.",
                },
                "content_type": {
                    "type": "STRING",
                    "description": "track | artist | album | playlist",
                },
                "device": {
                    "type": "STRING",
                    "description": "Optional Spotify Connect device name; normally omit to use the Raspberry Pi.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "jarvis_update",
        "description": (
            "Checks and installs official JARVIS Raspberry Pi updates from the configured GitHub "
            "repository. Use check when the user asks to search for updates. Use install only when "
            "the user explicitly confirms installation or clearly commands JARVIS to update itself. "
            "Never invent an available version and never install without explicit confirmation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "check | install | status",
                },
                "confirmed": {
                    "type": "BOOLEAN",
                    "description": "True only if the user explicitly approved installation.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "developer_mode",
        "description": (
            "Controls JARVIS developer mode. When the user says 'modo desarrollador' or the common "
            "misspelling 'modo desarroyador', call activate immediately; JARVIS will request the "
            "password locally in a masked written dialog. Use analyze to inspect recent errors and "
            "interaction history. Sensitive personality or voice changes require an active developer "
            "session and explicit user confirmation. Use audit to show the tamper-evident action log. "
            "Analysis never applies a fix. This tool never accepts the password as an argument."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "activate | analyze | audit | status | disable | set_personality | reset_personality | set_voice",
                },
                "personality": {
                    "type": "STRING",
                    "description": "Requested speaking style and personality preferences, without removing core safety rules.",
                },
                "voice": {
                    "type": "STRING",
                    "description": "A supported Gemini prebuilt voice name.",
                },
                "confirmed": {
                    "type": "BOOLEAN",
                    "description": "True only after the user explicitly confirms a sensitive change.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "export_diagnostics",
        "description": (
            "Creates a complete redacted JARVIS diagnostic TXT including recent conversation context and "
            "temporarily shares it through a private local-network download link. Use when the user "
            "asks to export, send, share, or prepare JARVIS errors for support."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": "Shuts down the assistant when the user clearly asks to stop, quit, close, or end JARVIS.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "save_memory",
        "description": "Silently saves durable user facts, preferences, projects, goals, and notes to memory.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "identity | preferences | projects | relationships | wishes | notes"},
                "key": {"type": "STRING", "description": "Short snake_case key."},
                "value": {"type": "STRING", "description": "Concise value in English."},
            },
            "required": ["category", "key", "value"],
        },
    },
]


def _available_tool_declarations() -> list[dict]:
    if is_desktop_mode():
        disabled = {
            "pi_controls", "social_insights", "jarvis_update", "spotify_player",
            "export_diagnostics",
        }
        return [item for item in TOOL_DECLARATIONS if item.get("name") not in disabled]
    return TOOL_DECLARATIONS


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self.mic_raw_queue  = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._spotify_control_lock = threading.Lock()
        self._update_control_lock = threading.Lock()
        self._state_mtime   = 0.0
        self._last_state_check = 0.0
        self._pre_roll = deque(maxlen=12)
        self.updates = UpdateManager()
        self.developer = DeveloperMode()
        self._pending_update: ReleaseInfo | None = None
        self._restart_requested = False
        self._restart_fallback_started = False
        self._microphone_available: bool | None = None
        self._speaker_available: bool | None = None
        self._input_device = _configured_audio_device("INPUT_DEVICE")
        self._output_device = _configured_audio_device("OUTPUT_DEVICE")
        self._input_device_generation = 0
        self._output_device_generation = 0
        self._input_stream_open = False
        self._output_stream_open = False
        self._audio_backend_refreshing = False
        self._audio_backend_refresh_task = None
        self._followup_listen_seconds = max(
            1.0, float(get_secret("FOLLOWUP_LISTEN_SECONDS", "5"))
        )
        configured_wake_threshold = get_secret("WAKE_THRESHOLD", "0.40").strip()
        # 0.55 was the old shipped default. Migrate that exact legacy value to
        # the calibrated default while preserving every custom value.
        if configured_wake_threshold in {"", "0.55"}:
            configured_wake_threshold = "0.40"
        self.wake_gate = WakeWordGate(
            mode=get_secret("WAKE_MODE", "wakeword").lower(),
            threshold=float(configured_wake_threshold),
            conversation_seconds=float(get_secret("CONVERSATION_TIMEOUT_SECONDS", "12")),
            voice_rms_threshold=int(get_secret("VOICE_RMS_THRESHOLD", "300")),
            confirmation_frames=int(get_secret("WAKE_CONFIRM_FRAMES", "2")),
            vad_threshold=float(get_secret("WAKE_VAD_THRESHOLD", "0")),
            auto_gain=get_secret("WAKE_AUTO_GAIN", "1").strip().lower()
            not in {"0", "false", "no", "off"},
        )
        self.ui.on_text_command = self._on_text_command
        self.ui.on_manual_activate = self._manual_activate
        if hasattr(self.ui, "on_audio_devices_changed"):
            self.ui.on_audio_devices_changed = self._on_audio_devices_changed
        if hasattr(self.ui, "on_audio_refresh_requested"):
            self.ui.on_audio_refresh_requested = self._on_audio_refresh_requested
        if hasattr(self.ui, "on_persona_settings_changed"):
            self.ui.on_persona_settings_changed = self._on_persona_settings_changed
        if hasattr(self.ui, "on_spotify_control"):
            self.ui.on_spotify_control = self._on_spotify_control
        if hasattr(self.ui, "on_update_requested"):
            self.ui.on_update_requested = self._on_update_requested
        if hasattr(self.ui, "on_diagnostic_export_requested"):
            self.ui.on_diagnostic_export_requested = self._on_diagnostic_export_requested
        # Muting is a temporary privacy control for the current session. A
        # previous shutdown must never leave the appliance muted on next boot.
        listening_state.set_listening_muted(False)
        self.ui.muted = False
        if self.wake_gate.mode == "wakeword" and not self.wake_gate.available:
            self.ui.write_log(f"ERR: Local wake word unavailable: {self.wake_gate.error}")
            self.ui.write_log("SYS: Privacy fallback active; use typed commands until it is repaired.")
        elif self.wake_gate.error:
            self.ui.write_log(f"WARN: {self.wake_gate.error}")
        if hasattr(self.ui, "set_wake_status"):
            self.ui.set_wake_status(self.wake_gate.health_snapshot())

    def _on_audio_devices_changed(self, input_device, output_device) -> None:
        try:
            input_device = int(input_device) if input_device is not None else None
        except (TypeError, ValueError):
            input_device = None
        try:
            output_device = int(output_device) if output_device is not None else None
        except (TypeError, ValueError):
            output_device = None

        if input_device != self._input_device:
            self._input_device = input_device
            self._input_device_generation += 1
            self.ui.write_log("SYS: Dispositivo de entrada actualizado.")

        if output_device != self._output_device:
            self._output_device = output_device
            self._output_device_generation += 1
            self.ui.write_log("SYS: Dispositivo de salida actualizado.")
            if self._loop is not None and self.audio_in_queue is not None:
                self._loop.call_soon_threadsafe(self._request_output_stream_restart)

    def _request_output_stream_restart(self) -> None:
        if self.audio_in_queue is None:
            return
        while not self.audio_in_queue.empty():
            try:
                self.audio_in_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.audio_in_queue.put_nowait(None)

    def _on_audio_refresh_requested(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._schedule_audio_backend_refresh)

    def _schedule_audio_backend_refresh(self) -> None:
        if self._audio_backend_refresh_task and not self._audio_backend_refresh_task.done():
            return
        self._audio_backend_refresh_task = asyncio.create_task(
            self._refresh_audio_backend()
        )

    async def _refresh_audio_backend(self) -> None:
        self._audio_backend_refreshing = True
        self._input_device_generation += 1
        self._output_device_generation += 1
        self._request_output_stream_restart()
        deadline = time.monotonic() + 2.0
        while (
            self._input_stream_open or self._output_stream_open
        ) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        try:
            if self._input_stream_open or self._output_stream_open:
                raise RuntimeError("los flujos de audio no se cerraron a tiempo")
            await asyncio.to_thread(_restart_portaudio_backend)
            refresh_ui = getattr(self.ui, "refresh_audio_devices", None)
            if refresh_ui is not None:
                refresh_ui()
            self.ui.write_log("SYS: Lista de dispositivos de audio actualizada.")
        except Exception as exc:
            self.ui.write_log(f"ERR: No se pudo actualizar el audio: {exc}")
        finally:
            self._audio_backend_refreshing = False

    def _manual_activate(self):
        if self.ui.muted:
            self.set_listening_muted(False)
        self.wake_gate.activate()
        self.ui.set_state("LISTENING")
        self.ui.write_log("SYS: Conversation opened manually.")

    def _set_developer_ui(self, active: bool) -> None:
        setter = getattr(self.ui, "set_developer_unlocked", None)
        if setter is not None:
            setter(bool(active))

    def _audit_developer(
        self,
        action: str,
        outcome: str,
        details: dict | None = None,
        changes: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        event_id = append_developer_audit(action, outcome, details, changes)
        self.ui.write_log(f"DEV: {event_id} · {outcome} · {action}")
        return event_id

    async def _request_developer_password(self) -> tuple[bool, str]:
        requester = getattr(self.ui, "request_developer_password", None)
        if requester is None:
            self._audit_developer("activate", "failed", {"reason": "password dialog unavailable"})
            return False, "La interfaz no permite introducir la contraseña localmente."
        password = await asyncio.to_thread(requester)
        if password is None:
            self._audit_developer("activate", "cancelled")
            return False, "Activación cancelada."
        allowed, message = self.developer.verify(password)
        self._audit_developer(
            "activate",
            "authorized" if allowed else "rejected",
            {"message": message},
        )
        self._set_developer_ui(allowed)
        if allowed:
            def relock_when_expired():
                time.sleep(self.developer.remaining_seconds + 1)
                if not self.developer.active:
                    self._set_developer_ui(False)

            threading.Thread(target=relock_when_expired, daemon=True).start()
        self.ui.write_log(
            "SYS: Modo desarrollador activado."
            if allowed else f"WARN: {message}"
        )
        return allowed, message

    def _on_persona_settings_changed(self, personality: str, voice: str) -> None:
        if not self.developer.active:
            self._set_developer_ui(False)
            self._audit_developer(
                "settings.persona",
                "rejected",
                {"reason": "developer mode inactive"},
            )
            self.ui.write_log(
                "WARN: Activa primero el modo desarrollador diciendo 'Hey Jarvis, modo desarrollador'."
            )
            return
        try:
            previous_personality = read_personality_style()
            previous_voice = configured_voice()
            write_personality_style(personality)
            selected_voice = write_voice(voice)
            changes = []
            if personality.strip() != previous_personality:
                changes.append("config/personality_style.txt")
            if selected_voice != previous_voice:
                changes.append(".env:JARVIS_VOICE")
            event_id = self._audit_developer(
                "settings.persona",
                "applied",
                {
                    "personality_before": previous_personality,
                    "personality_after": personality.strip(),
                    "voice_before": previous_voice,
                    "voice_after": selected_voice,
                },
                changes,
            )
            self.ui.write_log(
                f"SYS: Personalidad y voz {selected_voice} guardadas ({event_id}). Reiniciando Jarvis..."
            )
            threading.Thread(target=self._exit_for_update, daemon=True).start()
        except Exception as exc:
            self._audit_developer(
                "settings.persona",
                "failed",
                {"error": str(exc)},
            )
            self.ui.write_log(f"ERR: No se pudo guardar la personalización: {exc}")

    def _sync_external_listening_state(self):
        now = time.monotonic()
        if now - self._last_state_check < 0.5:
            return
        self._last_state_check = now
        try:
            mtime = listening_state.STATE_FILE.stat().st_mtime
        except FileNotFoundError:
            return
        except Exception:
            return
        if mtime <= self._state_mtime:
            return
        self._state_mtime = mtime
        muted = listening_state.get_listening_muted(self.ui.muted)
        if muted != self.ui.muted:
            self.ui.muted = muted
            self.ui.set_state("MUTED" if muted else "LISTENING")
            self.ui.write_log("SYS: Listening muted by control file." if muted else "SYS: Listening resumed by control file.")

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            self.ui.write_log("ERR: Gemini no esta conectado. Reintentando la conexion...")
            self.ui.set_state("STANDBY")
            return
        self.ui.set_state("THINKING")
        future = asyncio.run_coroutine_threadsafe(
            self.session.send_realtime_input(text=text), self._loop
        )
        future.add_done_callback(self._text_send_finished)

    def _on_spotify_control(self, action: str):
        with self._spotify_control_lock:
            if action != "refresh":
                result = spotify_control({"action": action})
                self.ui.write_log(f"SPOTIFY: {result}")
            state = spotify_snapshot()
            if hasattr(self.ui, "set_spotify_state"):
                self.ui.set_spotify_state(state)
            return state

    def _on_update_requested(self, action: str) -> dict:
        with self._update_control_lock:
            if action == "check":
                check = self.updates.check_for_updates()
                if check.available and check.release:
                    self._pending_update = check.release
                    notes = check.release.notes.strip()
                    detail = f"\n\n{notes[:700]}" if notes else ""
                    return {
                        "status": "available",
                        "message": (
                            f"Jarvis {check.release.version} está disponible; "
                            f"tienes instalada la versión {check.current_version}.{detail}"
                        ),
                    }
                self._pending_update = None
                return {
                    "status": "current",
                    "message": f"Jarvis ya está actualizado en la versión {check.current_version}.",
                }
            if action == "install":
                release = self._pending_update
                if release is None:
                    check = self.updates.check_for_updates()
                    release = check.release if check.available else None
                if release is None:
                    return {"status": "current", "message": "Jarvis ya está actualizado."}
                installed = self.updates.install(release)
                self._pending_update = None

                def restart_after_dialog():
                    time.sleep(3)
                    self._exit_for_update()

                threading.Thread(target=restart_after_dialog, daemon=True).start()
                return {
                    "status": "installed",
                    "message": (
                        f"Jarvis {installed.installed_version} se instaló y validó correctamente. "
                        "La aplicación se reiniciará ahora."
                    ),
                }
            return {"status": "error", "message": f"Acción de actualización desconocida: {action}"}

    def _on_diagnostic_export_requested(self) -> dict:
        report = create_diagnostic_report(
            {
                "assistant_state": "MUTED" if self.ui.muted else "ACTIVE",
                "microphone_available": self._microphone_available,
                "speaker_available": self._speaker_available,
                "input_device": self._input_device,
                "output_device": self._output_device,
                "wake_mode": self.wake_gate.mode,
                "wake_available": self.wake_gate.available,
                "wake_error": self.wake_gate.error,
                "wake_telemetry": self.wake_gate.health_snapshot(),
            }
        )
        shared = share_diagnostic_report(report)
        self.ui.write_log(f"SYS: Diagnóstico seguro creado: {report.name}")
        return {
            "status": "ready",
            "path": shared["path"],
            "url": shared["url"],
            "expires_seconds": shared["expires_seconds"],
            "message": (
                "Informe TXT seguro preparado. Ábrelo desde otro dispositivo de la misma red "
                "usando el enlace temporal y adjunta el archivo en el chat de Codex."
            ),
        }

    def _text_send_finished(self, future):
        try:
            future.result()
        except Exception as exc:
            self.ui.write_log(f"ERR: No se pudo enviar la orden: {exc}")
            self.ui.set_state("STANDBY")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            was_speaking = self._is_speaking
            self._is_speaking = value
        if value:
            if self.ui.muted:
                return
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            if was_speaking:
                self.wake_gate.activate_for(self._followup_listen_seconds)
                self.ui.write_log(
                    f"SYS: Escucha contextual abierta {self._followup_listen_seconds:g} segundos."
                )
            self.ui.set_state("LISTENING" if self.wake_gate.active else "STANDBY")
            if was_speaking and self._restart_requested:
                self._restart_requested = False
                threading.Thread(target=self._exit_for_update, daemon=True).start()

    @staticmethod
    def _exit_for_update():
        # Exit code 75 is handled by start_jarvis_pi.sh, which keeps the X
        # session alive and starts the newly installed code.
        time.sleep(1.0)
        os._exit(75)

    def _request_restart_after_response(self):
        self._restart_requested = True
        if self._restart_fallback_started:
            return
        self._restart_fallback_started = True

        def fallback():
            # A muted/broken speaker may never produce a speaking transition.
            # Do not leave a successfully installed update pending forever.
            time.sleep(30)
            if self._restart_requested:
                os._exit(75)

        threading.Thread(target=fallback, daemon=True).start()

    def set_listening_muted(self, value: bool, reason: str = "") -> str:
        listening_state.set_listening_muted(value)
        try:
            self._state_mtime = listening_state.STATE_FILE.stat().st_mtime
        except Exception:
            pass
        self.ui.muted = value
        if value:
            self.ui.set_state("MUTED")
            self.ui.write_log("SYS: Listening muted. Say 'JARVIS wake up' to resume.")
            return "Listening muted. Say 'JARVIS wake up' to resume."
        self.ui.set_state("LISTENING")
        self.ui.write_log("SYS: Listening resumed.")
        return "Listening resumed."

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_realtime_input(text=text),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _queue_mic_chunk(self, data: bytes):
        if not self.out_queue:
            return
        payload = {"data": data, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
        try:
            if self.out_queue.full():
                self.out_queue.get_nowait()
            self.out_queue.put_nowait(payload)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass

    def _queue_raw_mic_chunk(self, data: bytes):
        if not self.mic_raw_queue:
            return
        try:
            if self.mic_raw_queue.full():
                self.mic_raw_queue.get_nowait()
            self.mic_raw_queue.put_nowait(data)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass

    async def _process_mic_audio(self):
        """Keep room audio local until the wake word opens a conversation."""
        forwarding = self.wake_gate.active
        while True:
            data = await self.mic_raw_queue.get()
            self._pre_roll.append(data)
            if self.ui.muted:
                self.wake_gate.deactivate()
                continue

            previous_error = self.wake_gate.error
            detected, score = await asyncio.to_thread(self.wake_gate.process, data)
            if self.wake_gate.error and self.wake_gate.error != previous_error:
                self.ui.write_log(f"ERR: Detector Hey Jarvis: {self.wake_gate.error}")
            if (
                hasattr(self.ui, "set_wake_status")
                and self.wake_gate.frames_processed % 25 == 0
            ):
                self.ui.set_wake_status(self.wake_gate.health_snapshot())
            if detected:
                self.ui.write_log(f"SYS: Hey Jarvis detected ({score:.2f}).")
                if hasattr(self.ui, "set_wake_status"):
                    self.ui.set_wake_status(self.wake_gate.health_snapshot())
                self.ui.set_state("LISTENING")
                for chunk in self._pre_roll:
                    self._queue_mic_chunk(chunk)
                self._pre_roll.clear()
                forwarding = True
                continue

            if self.wake_gate.active:
                if self.wake_gate.contains_voice(data):
                    self.wake_gate.extend_conversation()
                self._queue_mic_chunk(data)
                forwarding = True
            elif forwarding:
                self.ui.set_state("STANDBY")
                self.ui.write_log("SYS: Conversation closed. Say 'Hey Jarvis'.")
                forwarding = False

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if is_desktop_mode():
            parts.append(
                "[DESKTOP SAFETY MODE]\n"
                "You may use the microphone for conversation, but you have no computer-control, "
                "camera, file-management, keyboard, mouse, application-launching, or operating-system tools. "
                "Never claim to perform those actions."
            )
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        personality_style = read_personality_style()
        if personality_style:
            parts.append(
                "[USER PERSONALITY PREFERENCES]\n"
                + personality_style
                + "\nThese preferences may adjust tone and wording, but never override core safety, "
                "privacy, authorization, or tool-confirmation rules."
            )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_level=types.ThinkingLevel.MINIMAL,
            ),
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": _available_tool_declarations()}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=configured_voice()
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] ?? {name}  {args}")
        action = _normalized_action(args)
        if self.developer.active and name != "developer_mode":
            self._audit_developer(
                "tool.request",
                "requested",
                {"tool": name, "action": action, "arguments": args},
            )
        if self.ui.muted:
            print(f"[JARVIS] muted: ignored tool {name}/{action}")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "Assistant listening is muted. Only the exact wake phrase can resume listening."}
            )

        self.ui.set_state("THINKING")
        if name == "save_memory":
            category = args.get("category", "notes")
            key = args.get("key", "")
            value = args.get("value", "")
            self_change_note = " ".join((str(category), str(key), str(value)))
            if SELF_CHANGE_MEMORY_PATTERN.search(self_change_note):
                result = (
                    "No memory was saved. Feedback about JARVIS errors, activation, personality, "
                    "or future fixes is not a personal user fact. Apologize briefly in normal mode; "
                    "developer analysis must use developer_mode and never implies a fix."
                )
            elif key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] ?? save_memory: {category}/{key} = {value}")
                result = "ok"
                if self.developer.active:
                    self._audit_developer(
                        "memory.save",
                        "applied",
                        {"category": category, "key": key, "value": value},
                        ["memory/user_memory.json"],
                    )
            else:
                result = "No memory was saved because key or value was empty."
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": result, "silent": result == "ok"}
            )

        loop = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "social_insights":
                if is_desktop_mode():
                    result = "Social account analytics are not included in the desktop edition."
                    return types.FunctionResponse(id=fc.id, name=name, response={"result": result})
                from importlib import import_module

                zernio_social = import_module("omar_ai_core.tools.social_metrics").zernio_social
                if not args.get("action"):
                    args["action"] = "ask"
                r = await loop.run_in_executor(None, lambda: zernio_social(parameters=args, player=self.ui))
                result = r or "No social analytics data was returned."

            elif name == "pi_controls":
                if is_desktop_mode():
                    result = "Computer controls are disabled in the desktop edition."
                    return types.FunctionResponse(id=fc.id, name=name, response={"result": result})
                if action in LISTENING_MUTE_ACTIONS:
                    result = self.set_listening_muted(True)
                    return types.FunctionResponse(
                        id=fc.id, name=name,
                        response={"result": result}
                    )
                if action in LISTENING_UNMUTE_ACTIONS:
                    result = self.set_listening_muted(False)
                    return types.FunctionResponse(
                        id=fc.id, name=name,
                        response={"result": result}
                    )
                if action in SPEAKER_MUTE_ACTIONS:
                    args["action"] = "mute"
                elif action in SPEAKER_UNMUTE_ACTIONS:
                    args["action"] = "unmute"
                r = await loop.run_in_executor(None, lambda: pi_controls(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "home_control":
                r = await loop.run_in_executor(None, lambda: home_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "spotify_player":
                if is_desktop_mode():
                    result = "Spotify Connect control is currently available only on the Raspberry Pi edition."
                    return types.FunctionResponse(id=fc.id, name=name, response={"result": result})
                r = await loop.run_in_executor(None, lambda: spotify_control(parameters=args, player=self.ui))
                result = r or "No Spotify result was returned."
                state = await loop.run_in_executor(None, spotify_snapshot)
                if hasattr(self.ui, "set_spotify_state"):
                    self.ui.set_spotify_state(state)

            elif name == "jarvis_update":
                if is_desktop_mode():
                    result = "Remote self-updates are currently available only on the Raspberry Pi edition."
                    return types.FunctionResponse(id=fc.id, name=name, response={"result": result})
                update_action = action or "check"
                if update_action == "status":
                    status = self.updates.status()
                    result = (
                        f"JARVIS version {status.get('installed_version', 'unknown')}; "
                        f"update state: {status.get('status', 'idle')}."
                    )
                elif update_action == "check":
                    self.ui.write_log("SYS: Buscando actualizaciones seguras en GitHub...")
                    check = await loop.run_in_executor(None, self.updates.check_for_updates)
                    if check.available and check.release:
                        self._pending_update = check.release
                        notes = check.release.notes.strip()
                        summary = f" Changes: {notes[:500]}" if notes else ""
                        result = (
                            f"JARVIS {check.release.version} is available; the installed version is "
                            f"{check.current_version}.{summary} Ask the user to confirm before installing."
                        )
                    else:
                        self._pending_update = None
                        result = f"JARVIS is already up to date at version {check.current_version}."
                elif update_action == "install":
                    if not bool(args.get("confirmed")):
                        result = "Installation was not started. Ask the user for explicit confirmation first."
                    else:
                        release = self._pending_update
                        if release is None:
                            check = await loop.run_in_executor(None, self.updates.check_for_updates)
                            release = check.release if check.available else None
                        if release is None:
                            result = "JARVIS is already up to date; nothing was installed."
                        else:
                            self.ui.write_log(
                                f"SYS: Instalando JARVIS {release.version}; no desconectes la alimentación..."
                            )
                            installed = await loop.run_in_executor(
                                None, lambda: self.updates.install(release)
                            )
                            self._pending_update = None
                            self._request_restart_after_response()
                            result = (
                                f"JARVIS {installed.installed_version} was installed and validated. "
                                "Tell the user it will restart now."
                            )
                else:
                    result = "Unknown update action. Use check, install, or status."

            elif name == "export_diagnostics":
                if is_desktop_mode():
                    result = "Diagnostic export is currently available only on the Raspberry Pi edition."
                else:
                    exported = await loop.run_in_executor(
                        None, self._on_diagnostic_export_requested
                    )
                    result = (
                        f"Diagnostic report ready at {exported['url']}. "
                        f"The private local link expires in {exported['expires_seconds']} seconds. "
                        "Tell the user to open it from a device on the same network and attach the TXT to Codex."
                    )

            elif name == "developer_mode":
                developer_action = action or "status"
                if developer_action == "activate":
                    allowed, message = await self._request_developer_password()
                    if allowed:
                        event_id = self._audit_developer(
                            "diagnostics.snapshot",
                            "analysis_only",
                            {"reason": "automatic snapshot after activation"},
                        )
                        result = (
                            f"{message} The password was checked locally and must never be repeated. "
                            "Analyze the following diagnostic snapshot now. Clearly separate confirmed "
                            "errors from possible improvements. This is analysis only: no error has been "
                            "fixed and no persistent change was made. Never say that anything was corrected. "
                            f"Audit event: {event_id}.\n\n" + diagnostic_snapshot()
                        )
                    else:
                        result = message
                elif developer_action == "status":
                    event_id = self._audit_developer(
                        "status",
                        "read_only",
                        {"active": self.developer.active},
                    )
                    if self.developer.active:
                        result = (
                            "Developer mode is active for approximately "
                            f"{max(1, self.developer.remaining_seconds // 60)} more minutes. Audit event: {event_id}."
                        )
                    else:
                        result = f"Developer mode is disabled. Audit event: {event_id}."
                elif developer_action == "disable":
                    event_id = self._audit_developer("disable", "applied")
                    self.developer.deactivate()
                    self._set_developer_ui(False)
                    result = f"Developer mode disabled. Audit event: {event_id}. No persistent setting changed."
                elif not self.developer.active:
                    self._set_developer_ui(False)
                    event_id = self._audit_developer(
                        developer_action,
                        "rejected",
                        {"reason": "developer mode inactive"},
                    )
                    result = (
                        "Developer mode is locked. Ask the user to say 'Hey Jarvis, modo desarrollador' "
                        f"and enter the password locally. Audit event: {event_id}."
                    )
                elif developer_action == "analyze":
                    event_id = self._audit_developer(
                        "diagnostics.analyze",
                        "analysis_only",
                        {"persistent_changes": False},
                    )
                    result = (
                        "Analyze this current JARVIS diagnostic snapshot. Explain likely root causes, "
                        "what JARVIS did poorly, and safe improvements. This action cannot apply a fix. "
                        "Do not say fixed, corrected, changed, learned, or saved. State clearly that no "
                        f"persistent change was made. Audit event: {event_id}.\n\n"
                        + diagnostic_snapshot()
                    )
                elif developer_action == "audit":
                    event_id = self._audit_developer("audit.read", "read_only")
                    result = (
                        f"Show this developer audit to the user. Audit read event: {event_id}.\n\n"
                        + (read_developer_audit() or "No developer actions have been recorded yet.")
                    )
                elif developer_action in {"set_personality", "reset_personality", "set_voice"}:
                    if not bool(args.get("confirmed")):
                        event_id = self._audit_developer(
                            developer_action,
                            "rejected",
                            {"reason": "explicit confirmation missing"},
                        )
                        result = (
                            "Sensitive change not applied. Ask for explicit confirmation first. "
                            f"Audit event: {event_id}."
                        )
                    elif developer_action == "set_voice":
                        previous_voice = configured_voice()
                        selected_voice = write_voice(str(args.get("voice") or ""))
                        changes = [".env:JARVIS_VOICE"] if selected_voice != previous_voice else []
                        event_id = self._audit_developer(
                            "voice.set",
                            "applied",
                            {"before": previous_voice, "after": selected_voice},
                            changes,
                        )
                        self._request_restart_after_response()
                        result = (
                            f"Voice changed to {selected_voice}. Audit event: {event_id}. "
                            "Only JARVIS_VOICE changed. JARVIS will restart after this response."
                        )
                    else:
                        previous_personality = read_personality_style()
                        personality = "" if developer_action == "reset_personality" else str(
                            args.get("personality") or ""
                        )
                        write_personality_style(personality)
                        changes = (
                            ["config/personality_style.txt"]
                            if personality.strip() != previous_personality else []
                        )
                        event_id = self._audit_developer(
                            "personality.reset" if developer_action == "reset_personality" else "personality.set",
                            "applied",
                            {"before": previous_personality, "after": personality.strip()},
                            changes,
                        )
                        self._request_restart_after_response()
                        result = (
                            f"Personality preferences saved. Audit event: {event_id}. "
                            "No source code or API connection was changed. JARVIS will restart after this response."
                        )
                else:
                    event_id = self._audit_developer(
                        developer_action,
                        "rejected",
                        {"reason": "unknown developer action"},
                    )
                    result = f"Unknown developer action. Audit event: {event_id}."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")

                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)

                threading.Thread(target=_shutdown, daemon=True).start()
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        if self.developer.active and name != "developer_mode":
            persistent_changes = []
            if name == "home_control":
                persistent_changes = [f"Home Assistant device state ({action or 'unknown'})"]
            elif name == "spotify_player" and action not in {"status", "current", "currently_playing", "devices", "list_devices"}:
                persistent_changes = [f"Spotify playback state ({action or 'unknown'})"]
            elif name == "pi_controls" and action not in {"status", "list"}:
                persistent_changes = [f"Raspberry Pi device state ({action or 'unknown'})"]
            elif name == "jarvis_update" and action == "install" and "installed" in str(result).lower():
                persistent_changes = ["JARVIS application files via verified update"]
            self._audit_developer(
                "tool.result",
                "failed" if str(result).startswith("Tool '") else "completed",
                {"tool": name, "action": action, "result": str(result)[:1500]},
                persistent_changes,
            )

        print(f"[JARVIS] ?? {name} ? {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(audio=msg)

    async def _listen_audio(self):
        print("[JARVIS] ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¤ Mic started")
        loop = asyncio.get_event_loop()
        generation = self._input_device_generation
        device = self._input_device
        stream_rate, stream_channels = _audio_stream_format(
            device, "input", SEND_SAMPLE_RATE
        )

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking:
                data = _convert_pcm16(
                    indata.tobytes(),
                    stream_rate,
                    SEND_SAMPLE_RATE,
                    stream_channels,
                    CHANNELS,
                )
                if not data:
                    return
                # Reuse the recognition PCM for visual analysis.  The UI does
                # not open a second microphone stream or compete for access.
                feed_visual = getattr(self.ui, "feed_input_audio", None)
                if feed_visual is not None:
                    feed_visual(data, SEND_SAMPLE_RATE)
                loop.call_soon_threadsafe(
                    self._queue_raw_mic_chunk,
                    data
                )

        try:
            with sd.InputStream(
                device=device,
                samplerate=stream_rate,
                channels=stream_channels,
                dtype="int16",
                blocksize=max(256, int(round(CHUNK_SIZE * stream_rate / SEND_SAMPLE_RATE))),
                callback=callback,
            ):
                print("[JARVIS] ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¤ Mic stream open")
                self._input_stream_open = True
                while True:
                    if generation != self._input_device_generation:
                        return
                    self._microphone_available = True
                    self._sync_external_listening_state()
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ÃƒÂ¢Ã‚ÂÃ…â€™ Mic: {e}")
            raise
        finally:
            self._input_stream_open = False

    async def _receive_audio(self):
        print("[JARVIS] ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ¢â‚¬Å¡ Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data and not self.ui.muted:
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = sc.output_transcription.text.strip()
                            if txt and not self.ui.muted:
                                self.set_speaking(True)
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)
                                if self.ui.muted and _is_wake_phrase(txt):
                                    self.set_listening_muted(False)
                                    out_buf = []
                                    while self.audio_in_queue and not self.audio_in_queue.empty():
                                        try:
                                            self.audio_in_queue.get_nowait()
                                        except asyncio.QueueEmpty:
                                            break

                        if sc.turn_complete:
                            self.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                if self.ui.muted:
                                    self.ui.write_log("SYS: Muted speech ignored.")
                                else:
                                    self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out and not self.ui.muted:
                                self.ui.write_log(f"Jarvis: {full_out}")
                            out_buf = []

                            # Disabled for the Pi appliance runtime: automatic memory
                            # extraction was causing slow background OpenRouter calls
                            # after normal voice turns.

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â¾ {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            print(f"[JARVIS] ÃƒÂ¢Ã‚ÂÃ…â€™ Recv: {e}")
            traceback.print_exc()
            raise

    async def _listen_audio_resilient(self):
        """Keep Gemini's text channel alive when audio input is unavailable."""
        while True:
            if getattr(self, "_audio_backend_refreshing", False):
                await asyncio.sleep(0.05)
                continue
            try:
                await self._listen_audio()
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                first_failure = self._microphone_available is not False
                self._microphone_available = False
                print(f"[JARVIS] Microphone unavailable; text mode active: {exc}")
                if first_failure:
                    self.ui.write_log(
                        "WARN: Microphone unavailable. Text commands remain active; "
                        "connect or configure INPUT_DEVICE to restore voice input."
                    )
                # Retry quietly so hot-plugging a microphone restores voice
                # without restarting Jarvis, but never disconnect Gemini.
                await asyncio.sleep(5.0)

    async def _play_audio_legacy(self):
        print("[JARVIS] ?? Play started")
        loop = asyncio.get_event_loop()

        stream = sd.RawOutputStream(
            device=int(get_secret("OUTPUT_DEVICE")) if get_secret("OUTPUT_DEVICE") else None,
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        print(f"[JARVIS] Audio output ready: {sd.query_devices(stream.device)['name']}")
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                if self.ui.muted:
                    while self.audio_in_queue and not self.audio_in_queue.empty():
                        try:
                            self.audio_in_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    continue
                print(f"[JARVIS] Playing Gemini audio ({len(chunk)} bytes initial chunk)")
                self.set_speaking(True)
                feed_visual = getattr(self.ui, "feed_output_audio", None)
                if feed_visual is not None:
                    feed_visual(chunk, RECEIVE_SAMPLE_RATE)
                await asyncio.to_thread(stream.write, chunk)
                while True:
                    try:
                        chunk = await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.18)
                    except asyncio.TimeoutError:
                        self.set_speaking(False)
                        break
                    if self.ui.muted:
                        self.set_speaking(False)
                        break
                    if feed_visual is not None:
                        feed_visual(chunk, RECEIVE_SAMPLE_RATE)
                    await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ? Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def _play_audio(self):
        """Play Gemini PCM through the selected endpoint and reopen on changes."""
        generation = self._output_device_generation
        device = self._output_device
        stream_rate, stream_channels = _audio_stream_format(
            device, "output", RECEIVE_SAMPLE_RATE
        )
        stream = sd.RawOutputStream(
            device=device,
            samplerate=stream_rate,
            channels=stream_channels,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        try:
            stream.start()
            self._output_stream_open = True
            self._speaker_available = True
            info = sd.query_devices(stream.device)
            print(
                f"[JARVIS] Audio output ready: {info['name']} "
                f"({stream_rate} Hz, {stream_channels} ch)"
            )
            while True:
                chunk = await self.audio_in_queue.get()
                if chunk is None or generation != self._output_device_generation:
                    return
                if self.ui.muted:
                    continue
                self.set_speaking(True)
                feed_visual = getattr(self.ui, "feed_output_audio", None)
                if feed_visual is not None:
                    feed_visual(chunk, RECEIVE_SAMPLE_RATE)
                device_chunk = _convert_pcm16(
                    chunk,
                    RECEIVE_SAMPLE_RATE,
                    stream_rate,
                    CHANNELS,
                    stream_channels,
                )
                await asyncio.to_thread(stream.write, device_chunk)
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            self.audio_in_queue.get(), timeout=0.18
                        )
                    except asyncio.TimeoutError:
                        self.set_speaking(False)
                        break
                    if chunk is None or generation != self._output_device_generation:
                        return
                    if self.ui.muted:
                        self.set_speaking(False)
                        break
                    if feed_visual is not None:
                        feed_visual(chunk, RECEIVE_SAMPLE_RATE)
                    device_chunk = _convert_pcm16(
                        chunk,
                        RECEIVE_SAMPLE_RATE,
                        stream_rate,
                        CHANNELS,
                        stream_channels,
                    )
                    await asyncio.to_thread(stream.write, device_chunk)
        finally:
            self.set_speaking(False)
            self._output_stream_open = False
            with contextlib.suppress(Exception):
                stream.stop()
            with contextlib.suppress(Exception):
                stream.close()

    async def _play_audio_resilient(self):
        """Keep text mode alive and retry if the selected speaker is unavailable."""
        while True:
            if self._audio_backend_refreshing:
                await asyncio.sleep(0.05)
                continue
            try:
                await self._play_audio()
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                first_failure = self._speaker_available is not False
                self._speaker_available = False
                print(f"[JARVIS] Audio output unavailable: {exc}")
                if first_failure:
                    self.ui.write_log(
                        "WARN: Salida de audio no disponible. Selecciona otra en Ajustes."
                    )
                while self.audio_in_queue and not self.audio_in_queue.empty():
                    try:
                        self.audio_in_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await asyncio.sleep(5.0)

    async def run(self):
        _configure_live_transport()
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        consecutive_failures = 0
        retry_delay = 3.0
        while True:
            try:
                print("[JARVIS] ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ…â€™ Connecting...")
                # Keep ERROR stable while retrying in the background. The
                # previous ERROR -> THINKING -> ERROR cycle every three
                # seconds looked like a broken, flickering interface.
                if consecutive_failures == 0:
                    self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self.mic_raw_queue  = asyncio.Queue(maxsize=40)

                    print("[JARVIS] ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Connected.")
                    consecutive_failures = 0
                    retry_delay = 3.0
                    self.ui.set_state("LISTENING" if self.wake_gate.active else "STANDBY")
                    mode_msg = "continuous listening" if self.wake_gate.mode == "continuous" else "say 'Hey Jarvis'"
                    self.ui.write_log(f"SYS: JARVIS online; {mode_msg}.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio_resilient())
                    tg.create_task(self._process_mic_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio_resilient())
                    
            except Exception as e:
                print(f"[JARVIS] ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â {e}")
                traceback.print_exc()
                # The kiosk normally hides its terminal. Persist the real
                # exception so the visible ERROR state can be diagnosed over
                # SSH without guessing whether Gemini, audio or networking
                # caused it.
                try:
                    with RUNTIME_LOG_PATH.open("a", encoding="utf-8") as handle:
                        handle.write(
                            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                            f"{type(e).__name__}: {e}\n"
                        )
                        traceback.print_exc(file=handle)
                except Exception:
                    pass
                self.ui.write_log(f"ERR: {type(e).__name__}: {str(e)[:160]}")

            self.set_speaking(False)
            self.session = None
            consecutive_failures += 1
            if consecutive_failures == 1:
                self.ui.set_state("ERROR")
            delay = retry_delay
            print(f"[JARVIS] Reconnecting in {delay:.0f}s...")
            await asyncio.sleep(delay)
            retry_delay = min(retry_delay * 2.0, 30.0)

def main():
    try:
        (PROJECT_DIR / "assistant.pid").write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        print(f"[JARVIS] PID write failed: {e}")

    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\nÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â´ Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
