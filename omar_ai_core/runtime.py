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
from .settings import BASE_DIR, get_secret, is_desktop_mode, require_secret
from .state import listening as listening_state
from .audio.wakeword import WakeWordGate
from .updater import ReleaseInfo, UpdateManager


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

LISTENING_MUTE_ACTIONS = {"listening_mute", "assistant_mute", "mute_listening", "stop_listening"}
LISTENING_UNMUTE_ACTIONS = {"listening_unmute", "assistant_unmute", "unmute_listening", "start_listening"}
SPEAKER_MUTE_ACTIONS = {"speaker_mute", "mute_speaker", "volume_mute"}
SPEAKER_UNMUTE_ACTIONS = {"speaker_unmute", "unmute_speaker", "volume_unmute"}


def _get_api_key() -> str:
    return require_secret("GEMINI_API_KEY")


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
            "audio output, or speakers. Also controls volume, a configured Bluetooth speaker, and screen brightness. "
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
        disabled = {"pi_controls", "social_insights", "jarvis_update"}
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
        self._state_mtime   = 0.0
        self._last_state_check = 0.0
        self._pre_roll = deque(maxlen=12)
        self.updates = UpdateManager()
        self._pending_update: ReleaseInfo | None = None
        self._restart_requested = False
        self._restart_fallback_started = False
        self._microphone_available: bool | None = None
        self.wake_gate = WakeWordGate(
            mode=get_secret("WAKE_MODE", "wakeword").lower(),
            threshold=float(get_secret("WAKE_THRESHOLD", "0.55")),
            conversation_seconds=float(get_secret("CONVERSATION_TIMEOUT_SECONDS", "12")),
            voice_rms_threshold=int(get_secret("VOICE_RMS_THRESHOLD", "300")),
        )
        self.ui.on_text_command = self._on_text_command
        self.ui.on_manual_activate = self._manual_activate
        self.ui.muted = listening_state.get_listening_muted(False)
        if self.wake_gate.mode == "wakeword" and not self.wake_gate.available:
            self.ui.write_log(f"ERR: Local wake word unavailable: {self.wake_gate.error}")
            self.ui.write_log("SYS: Privacy fallback active; use typed commands until it is repaired.")
        elif self.wake_gate.error:
            self.ui.write_log(f"WARN: {self.wake_gate.error}")

    def _manual_activate(self):
        if self.ui.muted:
            self.set_listening_muted(False)
        self.wake_gate.activate()
        self.ui.set_state("LISTENING")
        self.ui.write_log("SYS: Conversation opened manually.")

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
                self.wake_gate.extend_conversation()
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

            detected, score = await asyncio.to_thread(self.wake_gate.process, data)
            if detected:
                self.ui.write_log(f"SYS: Hey Jarvis detected ({score:.2f}).")
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
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] ?? {name}  {args}")
        action = _normalized_action(args)
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
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] ?? save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
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

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking:
                data = indata.tobytes()
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
                device=int(get_secret("INPUT_DEVICE")) if get_secret("INPUT_DEVICE") else None,
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¤ Mic stream open")
                while True:
                    self._microphone_available = True
                    self._sync_external_listening_state()
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ÃƒÂ¢Ã‚ÂÃ…â€™ Mic: {e}")
            raise

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
            try:
                await self._listen_audio()
                return
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

    async def _play_audio(self):
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
                    tg.create_task(self._play_audio())
                    
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
