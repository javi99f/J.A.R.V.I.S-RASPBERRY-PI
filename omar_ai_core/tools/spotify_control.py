from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from omar_ai_core.settings import get_secret


SPOTIFY_SCOPES = " ".join(
    (
        "user-read-playback-state",
        "user-read-currently-playing",
        "user-modify-playback-state",
    )
)
SUPPORTED_SEARCH_TYPES = {"track", "artist", "album", "playlist"}


def _project_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _configured(value: str) -> bool:
    normalized = _clean(value).lower()
    return bool(normalized) and not normalized.startswith(
        ("your-", "tu-", "replace-", "change-me")
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).lower()).strip()


def _bounded_percent(value: object) -> int:
    try:
        return max(0, min(100, int(float(str(value)))))
    except (TypeError, ValueError) as exc:
        raise ValueError("Spotify volume must be a number from 0 to 100.") from exc


@dataclass(frozen=True, slots=True)
class SpotifySettings:
    client_id: str
    client_secret: str
    redirect_uri: str
    device_name: str
    device_id: str
    cache_path: Path

    @classmethod
    def load(cls) -> "SpotifySettings":
        cache_value = get_secret("SPOTIFY_CACHE_PATH")
        cache_path = (
            Path(cache_value).expanduser()
            if cache_value
            else _project_dir() / "config" / "spotify_token_cache.json"
        )
        return cls(
            client_id=get_secret("SPOTIFY_CLIENT_ID"),
            client_secret=get_secret("SPOTIFY_CLIENT_SECRET"),
            redirect_uri=get_secret(
                "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
            ),
            device_name=get_secret("SPOTIFY_DEVICE_NAME", "JARVIS Raspberry Pi"),
            device_id=get_secret("SPOTIFY_DEVICE_ID"),
            cache_path=cache_path,
        )

    def validate(self) -> None:
        missing = []
        if not _configured(self.client_id):
            missing.append("SPOTIFY_CLIENT_ID")
        if not _configured(self.client_secret):
            missing.append("SPOTIFY_CLIENT_SECRET")
        if missing:
            raise RuntimeError(
                "Spotify is not configured. Missing "
                + ", ".join(missing)
                + ". Run: cd ~/Jarvis && ./.venv/bin/python configure_spotify.py"
            )


class SpotifyController:
    def __init__(self, settings: SpotifySettings | None = None):
        self.settings = settings or SpotifySettings.load()

    def _client(self):
        self.settings.validate()
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
        except ImportError as exc:
            raise RuntimeError(
                "Spotipy is not installed. Run: cd ~/Jarvis && "
                "./.venv/bin/python -m pip install -r requirements.txt"
            ) from exc

        self.settings.cache_path.parent.mkdir(parents=True, exist_ok=True)
        auth = SpotifyOAuth(
            client_id=self.settings.client_id,
            client_secret=self.settings.client_secret,
            redirect_uri=self.settings.redirect_uri,
            scope=SPOTIFY_SCOPES,
            cache_path=str(self.settings.cache_path),
            open_browser=False,
        )
        token = auth.get_cached_token()
        if not token:
            raise RuntimeError(
                "Spotify authorization is pending. Run: cd ~/Jarvis && "
                "./.venv/bin/python configure_spotify.py"
            )
        return spotipy.Spotify(auth_manager=auth, requests_timeout=12, retries=2)

    @staticmethod
    def _devices(client) -> list[dict]:
        response = client.devices() or {}
        return [
            item
            for item in response.get("devices", [])
            if item.get("id") and not item.get("is_restricted")
        ]

    def _device(self, client, requested: str = "") -> dict:
        devices = self._devices(client)
        if not devices:
            raise RuntimeError(
                "No controllable Spotify Connect device is online. Check that raspotify is "
                "running and open Spotify once on your phone or computer."
            )

        requested_norm = _normalize(requested)
        configured_name = _normalize(self.settings.device_name)
        configured_id = _clean(self.settings.device_id)

        for device in devices:
            if configured_id and device.get("id") == configured_id:
                return device
        if requested_norm:
            for device in devices:
                if requested_norm in _normalize(device.get("name", "")):
                    return device
        if configured_name:
            for device in devices:
                if configured_name == _normalize(device.get("name", "")):
                    return device
            for device in devices:
                if configured_name in _normalize(device.get("name", "")):
                    return device
        for device in devices:
            if device.get("is_active"):
                return device
        return devices[0]

    @staticmethod
    def _device_line(device: dict) -> str:
        state = "active" if device.get("is_active") else "available"
        volume = device.get("volume_percent")
        volume_text = f", volume {volume}%" if volume is not None else ""
        return f"{device.get('name')} ({device.get('type', 'device')}): {state}{volume_text}"

    @staticmethod
    def _current_line(playback: dict | None) -> str:
        if not playback or not playback.get("item"):
            return "Spotify is connected, but nothing is currently selected."
        item = playback["item"]
        artists = ", ".join(
            artist.get("name", "") for artist in item.get("artists", []) if artist.get("name")
        )
        state = "playing" if playback.get("is_playing") else "paused"
        device = (playback.get("device") or {}).get("name", "an unknown device")
        by_artist = f" by {artists}" if artists else ""
        return f"Spotify is {state} {item.get('name', 'unknown track')}{by_artist} on {device}."

    @staticmethod
    def _snapshot(playback: dict | None) -> dict:
        if not playback or not playback.get("item"):
            return {
                "configured": True,
                "connected": True,
                "is_playing": False,
                "track": "Nada reproduciéndose",
                "artist": "Spotify",
                "device": "",
            }
        item = playback.get("item") or {}
        artists = ", ".join(
            artist.get("name", "") for artist in item.get("artists", []) if artist.get("name")
        )
        return {
            "configured": True,
            "connected": True,
            "is_playing": bool(playback.get("is_playing")),
            "track": item.get("name") or "Pista desconocida",
            "artist": artists or "Spotify",
            "device": (playback.get("device") or {}).get("name", ""),
        }

    def snapshot(self) -> dict:
        return self._snapshot(self._client().current_playback())

    def _play_search(self, client, query: str, search_type: str, device: dict) -> str:
        if not query:
            return "Tell me what song, artist, album, or playlist to play."
        kind = search_type if search_type in SUPPORTED_SEARCH_TYPES else "track"
        results = client.search(q=query, type=kind, limit=1) or {}
        bucket = results.get(f"{kind}s") or {}
        items = bucket.get("items") or []
        if not items:
            return f"I could not find a Spotify {kind} matching '{query}'."

        item = items[0]
        device_id = device["id"]
        if kind == "track":
            client.start_playback(device_id=device_id, uris=[item["uri"]])
        else:
            client.start_playback(device_id=device_id, context_uri=item["uri"])
        return f"Playing {item.get('name', query)} on {device.get('name')}."

    def execute(self, parameters: dict | None = None) -> str:
        params = parameters or {}
        action = _clean(params.get("action") or "status").lower().replace("-", "_").replace(" ", "_")
        query = _clean(params.get("query") or params.get("description"))
        requested_device = _clean(params.get("device"))
        client = self._client()

        if action in {"devices", "list_devices"}:
            devices = self._devices(client)
            if not devices:
                return "No controllable Spotify Connect devices are online."
            return "Spotify devices:\n" + "\n".join(self._device_line(item) for item in devices)

        if action in {"current", "currently_playing", "status"}:
            return self._current_line(client.current_playback())

        device = self._device(client, requested_device)
        device_id = device["id"]

        if action in {"play", "play_search", "search_and_play"}:
            kind = _clean(params.get("content_type") or params.get("type") or "track").lower()
            return self._play_search(client, query, kind, device)
        if action in {"resume", "continue"}:
            client.start_playback(device_id=device_id)
            return f"Spotify resumed on {device.get('name')}."
        if action in {"pause", "stop"}:
            client.pause_playback(device_id=device_id)
            return f"Spotify paused on {device.get('name')}."
        if action in {"next", "next_track", "skip"}:
            client.next_track(device_id=device_id)
            return "Playing the next Spotify track."
        if action in {"previous", "previous_track", "back"}:
            client.previous_track(device_id=device_id)
            return "Playing the previous Spotify track."
        if action in {"transfer", "select_device"}:
            client.transfer_playback(device_id=device_id, force_play=False)
            return f"Spotify playback is now assigned to {device.get('name')}."
        if action.startswith("volume") or action == "set_volume":
            return (
                "Spotify volume was not changed. JARVIS uses the Raspberry Pi system volume "
                "for every spoken volume request; call pi_controls instead."
            )

        return (
            "Unknown Spotify action. Use play, pause, resume, next, previous, current, "
            "devices, or transfer."
        )


def spotify_control(parameters: dict | None = None, player=None, **_) -> str:
    try:
        return SpotifyController().execute(parameters)
    except Exception as exc:
        message = _clean(exc)
        if "premium" in message.lower() or "restriction violated" in message.lower():
            return "Spotify playback control requires the app owner's active Spotify Premium account."
        return f"Spotify control failed: {message}"


def spotify_snapshot() -> dict:
    try:
        return SpotifyController().snapshot()
    except Exception as exc:
        message = _clean(exc)
        missing_config = "not configured" in message.lower() or "authorization is pending" in message.lower()
        return {
            "configured": not missing_config,
            "connected": False,
            "is_playing": False,
            "track": "Spotify sin configurar" if missing_config else "Spotify no disponible",
            "artist": "Abre ajustes o revisa la conexión",
            "device": "",
            "error": message,
        }
