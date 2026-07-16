"""Short visual and interaction QA harness for the desktop Jarvis core."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import psutil
from PIL import Image
from PyQt6.QtCore import QPoint, QTimer
from PyQt6.QtWidgets import QApplication

from omar_ai_core.display.liquid_window import LiquidMainWindow


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "qa"


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    window = LiquidMainWindow()
    window._ready = True
    window.settings.reduced_motion = False
    window.settings_panel.visibility.setValue(200)
    window.settings_panel.size.setValue(109)
    sent_commands: list[str] = []
    measurements: dict[str, float] = {}
    process = psutil.Process()
    window.on_text_command = sent_commands.append
    window.show()
    QTimer.singleShot(100, window.hide_panel)

    timeline = np.arange(960, dtype=np.float32) / 24000.0
    voice = (
        0.31 * np.sin(2.0 * np.pi * 155.0 * timeline)
        + 0.18 * np.sin(2.0 * np.pi * 720.0 * timeline)
        + 0.09 * np.sin(2.0 * np.pi * 3100.0 * timeline)
    )
    pcm = (np.clip(voice, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    audio_timer = QTimer(window)
    audio_timer.timeout.connect(lambda: window.analyzer.feed_output(pcm, 24000))

    def capture_idle_a() -> None:
        window.renderer.grab().save(str(OUTPUT / "ultron-idle-a.png"))
        process.cpu_percent(None)

    def capture_idle_b() -> None:
        window.renderer.grab().save(str(OUTPUT / "ultron-idle-b.png"))
        measurements["idle_fps"] = round(window.renderer.fps, 1)
        measurements["idle_cpu_percent"] = round(process.cpu_percent(None), 1)
        window.state_signal.emit("SPEAKING")
        audio_timer.start(40)
        process.cpu_percent(None)

    def capture_speaking() -> None:
        window.renderer.grab().save(str(OUTPUT / "ultron-speaking.png"))
        measurements["active_fps"] = round(window.renderer.fps, 1)
        measurements["active_cpu_percent"] = round(process.cpu_percent(None), 1)

    def show_context_menu() -> None:
        anchor = window.mapToGlobal(QPoint(window.width() // 2, window.renderer.height() // 2))
        QTimer.singleShot(220, capture_context_menu)
        window._show_context_menu(anchor)

    def capture_context_menu() -> None:
        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.grab().save(str(OUTPUT / "context-menu.png"))
            popup.close()

    def capture_settings() -> None:
        window.show_panel(force=True)
        window.toggle_settings()
        window.grab().save(str(OUTPUT / "visibility-size-settings.png"))

    def finish() -> None:
        idle_a = np.asarray(Image.open(OUTPUT / "ultron-idle-a.png").convert("RGBA"), dtype=np.int16)
        idle_b = np.asarray(Image.open(OUTPUT / "ultron-idle-b.png").convert("RGBA"), dtype=np.int16)
        idle_delta = np.max(np.abs(idle_a - idle_b), axis=2)
        measurements["idle_changed_pixels_percent"] = round(
            float(np.mean(idle_delta > 8) * 100.0), 1
        )
        report = {
            "renderer": window.renderer.renderer_name,
            "shader_error": window.renderer.shader_error_text,
            **measurements,
            "working_set_mb": round(process.memory_info().rss / (1024 * 1024), 1),
            "visibility_percent": round(window.settings.visibility * 100),
            "visible_core_diameter_px": 170,
            "idle_interval_ms": 62,
            "active_interval_ms": 33,
            "sent_commands": sent_commands,
            "size": [window.renderer.width(), window.renderer.height()],
        }
        (OUTPUT / "ultron-qa-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window.close()
        app.quit()

    QTimer.singleShot(1000, capture_idle_a)
    QTimer.singleShot(1850, capture_idle_b)
    QTimer.singleShot(3000, capture_speaking)
    QTimer.singleShot(3200, show_context_menu)
    QTimer.singleShot(3600, capture_settings)
    QTimer.singleShot(4200, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
