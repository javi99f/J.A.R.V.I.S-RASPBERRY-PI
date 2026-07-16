"""Benchmark a GPU-backed Qt Quick HUD prototype."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtWidgets import QApplication


def main() -> None:
    app = QApplication([])
    view = QQuickWidget()
    view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
    qml = Path(__file__).resolve().parent.parent / "assets" / "jarvis_hud.qml"
    view.setSource(QUrl.fromLocalFile(str(qml)))
    if view.status() == QQuickWidget.Status.Error:
        raise RuntimeError("; ".join(error.toString() for error in view.errors()))
    view.setWindowTitle("Jarvis Qt Quick GPU benchmark")
    view.resize(615, 868)
    view.show()

    root = view.rootObject()
    started_wall = time.perf_counter()
    started_cpu = time.process_time()

    def finish() -> None:
        wall = time.perf_counter() - started_wall
        cpu = time.process_time() - started_cpu
        print(json.dumps({
            "backend": "qtquick",
            "fps": round(float(root.property("measuredFps")), 1),
            "cpu_percent_total": round(cpu / wall / (os.cpu_count() or 1) * 100, 1),
            "cpu_core_equivalent": round(cpu / wall, 2),
            "graphics_api": str(view.quickWindow().rendererInterface().graphicsApi()),
        }))
        app.quit()

    QTimer.singleShot(7000, finish)
    app.exec()


if __name__ == "__main__":
    main()
