from __future__ import annotations

import threading
import time
from pathlib import Path

from .settings import BASE_DIR


HISTORY_FILE = BASE_DIR / "jarvis-history.log"
RUNTIME_LOG_FILE = BASE_DIR / "jarvis-runtime.log"
MAX_HISTORY_BYTES = 2 * 1024 * 1024
_lock = threading.Lock()


def _read_tail(path: Path, limit: int = MAX_HISTORY_BYTES) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        if size > limit and "\n" in text:
            text = text.split("\n", 1)[1]
        return text.strip()
    except OSError:
        return ""


def append_history(message: str) -> None:
    message = str(message or "").strip()
    if not message:
        return
    entry = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with _lock:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if HISTORY_FILE.exists() and HISTORY_FILE.stat().st_size > MAX_HISTORY_BYTES:
            recent = _read_tail(HISTORY_FILE, MAX_HISTORY_BYTES // 2)
            HISTORY_FILE.write_text(recent + ("\n" if recent else ""), encoding="utf-8")
        with HISTORY_FILE.open("a", encoding="utf-8") as handle:
            handle.write(entry)


def read_history() -> str:
    return _read_tail(HISTORY_FILE)


def read_diagnostics() -> str:
    content = _read_tail(RUNTIME_LOG_FILE, 256 * 1024)
    return content
