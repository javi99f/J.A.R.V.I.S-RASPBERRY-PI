import json
import os
import re
import subprocess
from pathlib import Path

from omar_ai_core.settings import get_secret


BASE_DIR = Path(__file__).resolve().parents[2]
BRIGHTNESS_STATE = BASE_DIR / "config" / "brightness_state.json"


def _run(cmd: list[str], timeout: int = 5):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _pipewire_env_cmd(cmd: str) -> list[str]:
    runtime_dir = os.getenv("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return ["bash", "-lc", f"export XDG_RUNTIME_DIR={runtime_dir}; {cmd}"]


def _write_brightness(value: int):
    BRIGHTNESS_STATE.parent.mkdir(parents=True, exist_ok=True)
    BRIGHTNESS_STATE.write_text(json.dumps({"brightness": value}), encoding="utf-8")


def _read_brightness(default: int = 100) -> int:
    try:
        data = json.loads(BRIGHTNESS_STATE.read_text(encoding="utf-8"))
        return max(10, min(100, int(data.get("brightness", default))))
    except Exception:
        return default


def _set_volume(value: int) -> str:
    value = max(0, min(100, int(value)))
    _run(_pipewire_env_cmd(f"pactl set-sink-volume @DEFAULT_SINK@ {value}%"))
    _run(_pipewire_env_cmd("pactl set-sink-mute @DEFAULT_SINK@ 0"))
    return f"Volume set to {value}%."


def _volume_delta(delta: int) -> str:
    sign = "+" if delta > 0 else "-"
    _run(_pipewire_env_cmd(f"pactl set-sink-volume @DEFAULT_SINK@ {sign}{abs(delta)}%"))
    _run(_pipewire_env_cmd("pactl set-sink-mute @DEFAULT_SINK@ 0"))
    return "Volume adjusted."


def _mute(value: bool) -> str:
    _run(_pipewire_env_cmd(f"pactl set-sink-mute @DEFAULT_SINK@ {'1' if value else '0'}"))
    return "Muted." if value else "Unmuted."


def _set_brightness(value: int) -> str:
    value = max(10, min(100, int(value)))
    _write_brightness(value)
    return f"JARVIS screen brightness set to {value}%."


def _brightness_delta(delta: int) -> str:
    return _set_brightness(_read_brightness() + delta)


def _connect_speaker() -> str:
    speaker_mac = get_secret("BLUETOOTH_SPEAKER_MAC")
    if not speaker_mac or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", speaker_mac):
        return "No Bluetooth speaker MAC is configured in BLUETOOTH_SPEAKER_MAC."
    mac_token = speaker_mac.replace(":", "_").upper()
    runtime_dir = os.getenv("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    script = f"""
export XDG_RUNTIME_DIR={runtime_dir}
bluetoothctl power on >/dev/null || true
bluetoothctl trust {speaker_mac} >/dev/null || true
if ! bluetoothctl info {speaker_mac} | grep -q 'Connected: yes'; then
  bluetoothctl connect {speaker_mac} >/dev/null || true
  sleep 4
fi
SINK_NAME=$(pactl list short sinks | awk '/bluez_output\\.{mac_token}/ {{print $2; exit}}')
SINK_ID=$(pactl list short sinks | awk '/bluez_output\\.{mac_token}/ {{print $1; exit}}')
if [ -n "$SINK_NAME" ]; then
  pactl set-default-sink "$SINK_NAME" || true
  pactl set-sink-volume "$SINK_NAME" 45% || true
  pactl set-sink-mute "$SINK_NAME" 0 || true
fi
if [ -n "$SINK_ID" ]; then
  pactl list short sink-inputs | awk '{{print $1}}' | while read -r input; do
    [ -n "$input" ] && pactl move-sink-input "$input" "$SINK_ID" || true
  done
fi
bluetoothctl info {speaker_mac} | grep -q 'Connected: yes'
"""
    result = _run(["bash", "-lc", script], timeout=15)
    if result.returncode == 0:
        return "Bluetooth speaker connected and selected."
    return "I could not connect the configured Bluetooth speaker."


def pi_controls(parameters: dict | None = None, player=None, **_) -> str:
    params = parameters or {}
    action = str(params.get("action") or "").lower().strip().replace("-", "_").replace(" ", "_")
    value = params.get("value")
    description = " ".join(str(params.get(k, "") or "") for k in ("description", "question", "command", "action"))

    if player and hasattr(player, "write_log"):
        player.write_log(f"[Pi] {action or description}")

    if action in {"volume_set", "set_volume"}:
        return _set_volume(int(value if value is not None else 45))
    if action in {"volume_up", "louder"}:
        return _volume_delta(10)
    if action in {"volume_down", "quieter"}:
        return _volume_delta(-10)
    if action in {"mute", "speaker_mute", "mute_speaker", "volume_mute"}:
        return _mute(True)
    if action in {"unmute", "speaker_unmute", "unmute_speaker", "volume_unmute"}:
        return _mute(False)
    if action in {"brightness_set", "set_brightness"}:
        return _set_brightness(int(value if value is not None else 70))
    if action in {"brightness_up", "brighter"}:
        return _brightness_delta(10)
    if action in {"brightness_down", "dim"}:
        return _brightness_delta(-10)
    if action in {"connect_era300", "connect_speaker", "speaker_era300"}:
        return _connect_speaker()

    return "Unknown Pi control. Try volume, brightness, mute, unmute, or connect speaker."
