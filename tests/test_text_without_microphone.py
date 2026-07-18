import asyncio
import unittest
from unittest.mock import AsyncMock

from omar_ai_core import runtime


class _TextOnlyUI:
    def __init__(self):
        self.logs = []
        self.logged = asyncio.Event()

    def write_log(self, message):
        self.logs.append(message)
        self.logged.set()


class TextWithoutMicrophoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_microphone_failure_keeps_text_session_alive(self):
        live = object.__new__(runtime.JarvisLive)
        live.ui = _TextOnlyUI()
        live._microphone_available = None
        live._listen_audio = AsyncMock(side_effect=OSError("no microphone"))

        task = asyncio.create_task(live._listen_audio_resilient())
        await asyncio.wait_for(live.ui.logged.wait(), timeout=1.0)

        self.assertFalse(task.done())
        self.assertFalse(live._microphone_available)
        self.assertTrue(any("Text commands remain active" in line for line in live.ui.logs))

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
