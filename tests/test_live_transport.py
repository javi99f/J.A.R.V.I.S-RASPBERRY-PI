import socket
import unittest
from unittest.mock import patch

import google.genai.live as live_module

from omar_ai_core import runtime


class _DummyWebSocket:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class LiveTransportTests(unittest.IsolatedAsyncioTestCase):
    async def _run_wrapper(self, fake_connect, environment):
        original_connect = live_module.ws_connect
        websocket = None

        def fake_secret(name, default=""):
            return environment.get(name, default)

        try:
            live_module.ws_connect = fake_connect
            with patch.object(runtime, "get_secret", side_effect=fake_secret):
                runtime._configure_live_transport()
                async with live_module.ws_connect(
                    "wss://example.test", ssl="context"
                ) as websocket:
                    self.assertFalse(websocket.closed)
        finally:
            live_module.ws_connect = original_connect
        self.assertIsNotNone(websocket)
        self.assertTrue(websocket.closed)

    async def test_pi_auto_transport_is_direct_and_retries_ipv4(self):
        calls = []

        async def fake_connect(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise TimeoutError("opening handshake")
            return _DummyWebSocket()

        await self._run_wrapper(
            fake_connect,
            {
                "APP_MODE": "raspberry_pi",
                "LIVE_OPEN_TIMEOUT_SECONDS": "45",
                "LIVE_IP_MODE": "auto",
                "LIVE_USE_SYSTEM_PROXY": "0",
            },
        )

        self.assertEqual(len(calls), 2)
        self.assertNotIn("family", calls[0][1])
        self.assertEqual(calls[1][1]["family"], socket.AF_INET)
        for _, kwargs in calls:
            self.assertEqual(kwargs["open_timeout"], 45.0)
            self.assertIsNone(kwargs["proxy"])
            self.assertEqual(kwargs["ssl"], "context")

    async def test_legacy_force_ipv4_prefers_it_but_keeps_auto_fallback(self):
        calls = []

        async def fake_connect(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise OSError("route unavailable")
            return _DummyWebSocket()

        await self._run_wrapper(
            fake_connect,
            {
                "APP_MODE": "raspberry_pi",
                "LIVE_IP_MODE": "",
                "LIVE_FORCE_IPV4": "1",
                "LIVE_USE_SYSTEM_PROXY": "0",
            },
        )

        self.assertEqual(calls[0][1]["family"], socket.AF_INET)
        self.assertNotIn("family", calls[1][1])

    async def test_pi_default_prefers_ipv4_without_removing_auto_fallback(self):
        calls = []

        async def fake_connect(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise TimeoutError("IPv4 edge unavailable")
            return _DummyWebSocket()

        with patch.object(runtime, "is_desktop_mode", return_value=False):
            await self._run_wrapper(
                fake_connect,
                {
                    "LIVE_IP_MODE": "",
                    "LIVE_FORCE_IPV4": "",
                    "LIVE_USE_SYSTEM_PROXY": "0",
                },
            )

        self.assertEqual(calls[0][1]["family"], socket.AF_INET)
        self.assertNotIn("family", calls[1][1])


if __name__ == "__main__":
    unittest.main()
