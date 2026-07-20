import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omar_ai_core.tools.spotify_control import SpotifyController, SpotifySettings, spotify_control


DEVICES = [
    {
        "id": "phone",
        "name": "Javier Phone",
        "type": "smartphone",
        "is_active": True,
        "is_restricted": False,
        "volume_percent": 40,
        "supports_volume": True,
    },
    {
        "id": "pi",
        "name": "JARVIS Raspberry Pi",
        "type": "speaker",
        "is_active": False,
        "is_restricted": False,
        "volume_percent": 35,
        "supports_volume": True,
    },
]


class FakeSpotify:
    def __init__(self, devices=None):
        self._devices = DEVICES if devices is None else devices
        self.calls = []

    def devices(self):
        return {"devices": self._devices}

    def search(self, q, type, limit):
        return {f"{type}s": {"items": [{"name": "Test Song", "uri": "spotify:track:test"}]}}

    def start_playback(self, **kwargs):
        self.calls.append(("play", kwargs))

    def pause_playback(self, **kwargs):
        self.calls.append(("pause", kwargs))

    def next_track(self, **kwargs):
        self.calls.append(("next", kwargs))

    def previous_track(self, **kwargs):
        self.calls.append(("previous", kwargs))

    def transfer_playback(self, **kwargs):
        self.calls.append(("transfer", kwargs))

    def volume(self, value, **kwargs):
        self.calls.append(("volume", value, kwargs))

    def current_playback(self):
        return None


class SpotifyControlTests(unittest.TestCase):
    def controller(self, client):
        settings = SpotifySettings(
            client_id="client",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8888/callback",
            device_name="JARVIS Raspberry Pi",
            device_id="",
            cache_path=Path(tempfile.gettempdir()) / "jarvis-test-spotify-cache.json",
        )
        controller = SpotifyController(settings)
        controller._client = lambda: client
        return controller

    def test_play_prefers_configured_pi_over_active_phone(self):
        client = FakeSpotify()
        result = self.controller(client).execute(
            {"action": "play", "query": "test song", "content_type": "track"}
        )
        self.assertIn("JARVIS Raspberry Pi", result)
        self.assertEqual(client.calls, [("play", {"device_id": "pi", "uris": ["spotify:track:test"]})])

    def test_spotify_volume_is_never_changed_directly(self):
        client = FakeSpotify()
        result = self.controller(client).execute({"action": "volume_up", "value": 15})
        self.assertIn("system volume", result)
        self.assertEqual(client.calls, [])

    def test_snapshot_contains_real_track_state(self):
        client = FakeSpotify()
        client.current_playback = lambda: {
            "is_playing": True,
            "item": {"name": "Test Song", "artists": [{"name": "Test Artist"}]},
            "device": {"name": "JARVIS Raspberry Pi"},
        }
        state = self.controller(client).snapshot()
        self.assertTrue(state["is_playing"])
        self.assertEqual(state["track"], "Test Song")
        self.assertEqual(state["artist"], "Test Artist")

    def test_missing_connect_device_is_actionable(self):
        client = FakeSpotify(devices=[])
        with self.assertRaisesRegex(RuntimeError, "raspotify"):
            self.controller(client).execute({"action": "pause"})

    @patch("omar_ai_core.tools.spotify_control.SpotifyController.execute")
    def test_public_tool_returns_errors_instead_of_crashing_runtime(self, execute):
        execute.side_effect = RuntimeError("authorization is pending")
        result = spotify_control({"action": "status"})
        self.assertIn("authorization is pending", result)


if __name__ == "__main__":
    unittest.main()
