from __future__ import annotations

import math
import struct
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QContextMenuEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
    QSurfaceFormat,
    QVector2D,
)
from PyQt6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFunctions_2_0,
    QOpenGLShader,
    QOpenGLShaderProgram,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from .assistant_state import AssistantState, normalize_state
from .audio_reactive import AudioFeatures, AudioReactiveAnalyzer
from .visual_config import VisualSettings


GL_COLOR_BUFFER_BIT = 0x00004000
GL_BLEND = 0x0BE2
GL_DEPTH_TEST = 0x0B71
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_FLOAT = 0x1406
GL_TRIANGLE_STRIP = 0x0005


def _asset_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


class LiquidGoldRenderer(QOpenGLWidget):
    clicked = pyqtSignal()
    context_requested = pyqtSignal(QPoint)
    shader_failed = pyqtSignal(str)

    def __init__(
        self,
        analyzer: AudioReactiveAnalyzer,
        settings: VisualSettings,
        parent=None,
    ):
        super().__init__(parent)
        surface_format = QSurfaceFormat()
        surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        surface_format.setVersion(2, 0)
        surface_format.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        surface_format.setAlphaBufferSize(8)
        surface_format.setDepthBufferSize(0)
        surface_format.setStencilBufferSize(0)
        surface_format.setSwapInterval(1)
        self.setFormat(surface_format)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

        self.analyzer = analyzer
        self.settings = settings
        self._state = AssistantState.IDLE
        self._state_mix = {state: 0.0 for state in AssistantState}
        self._state_mix[AssistantState.IDLE] = 1.0
        self._features = AudioFeatures()
        self._program: QOpenGLShaderProgram | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._functions: QOpenGLFunctions_2_0 | None = None
        self._shader_error = ""
        self._software_fallback = False
        self._start_time = time.perf_counter()
        self._last_tick = self._start_time
        self._fps_window_started = self._start_time
        self._frames = 0
        self._fps = 0.0
        self._press_global: QPoint | None = None
        self._window_origin: QPoint | None = None
        self._dragged = False

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._animate)
        self._timer.start(42)

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def renderer_name(self) -> str:
        return (
            "SOFTWARE CORE"
            if self._software_fallback
            else "OPENGL GLSL // ULTRON AMBER CORE"
        )

    @property
    def shader_error_text(self) -> str:
        return self._shader_error

    def set_state(self, state: str | AssistantState) -> None:
        self._state = normalize_state(state)

    def apply_settings(self, settings: VisualSettings) -> None:
        self.settings = settings.validate()
        self.analyzer.set_sensitivity(settings.microphone_sensitivity)

    def initializeGL(self) -> None:
        try:
            self._functions = QOpenGLFunctions_2_0()
            if not self._functions.initializeOpenGLFunctions():
                raise RuntimeError("OpenGL 2.0 functions are unavailable")
            self._functions.glDisable(GL_DEPTH_TEST)
            self._functions.glEnable(GL_BLEND)
            self._functions.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

            shader_dir = _asset_root() / "assets" / "shaders"
            vertex_source = (shader_dir / "ultron_core.vert").read_text(encoding="utf-8")
            fragment_source = (shader_dir / "ultron_core.frag").read_text(encoding="utf-8")
            program = QOpenGLShaderProgram(self)
            if not program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Vertex, vertex_source
            ):
                raise RuntimeError(f"Vertex shader: {program.log()}")
            if not program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Fragment, fragment_source
            ):
                raise RuntimeError(f"Fragment shader: {program.log()}")
            program.bindAttributeLocation("a_position", 0)
            if not program.link():
                raise RuntimeError(f"Shader link: {program.log()}")

            vertices = struct.pack("8f", -1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
            vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            if not vbo.create():
                raise RuntimeError("Unable to create the OpenGL vertex buffer")
            vbo.bind()
            vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
            vbo.allocate(vertices, len(vertices))
            vbo.release()
            self._program = program
            self._vbo = vbo
            self.context().aboutToBeDestroyed.connect(self.cleanup)
        except Exception as exc:
            self._shader_error = str(exc)
            self._software_fallback = True
            print(f"[JARVIS] Liquid shader unavailable; software fallback active: {exc}")
            QTimer.singleShot(0, lambda: self.shader_failed.emit(self._shader_error))

    def cleanup(self) -> None:
        if not self.context() or not self.context().isValid():
            return
        self.makeCurrent()
        if self._vbo is not None and self._vbo.isCreated():
            self._vbo.destroy()
        self._vbo = None
        self._program = None
        self.doneCurrent()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self.cleanup()
        super().closeEvent(event)

    def _target_interval(self) -> int:
        if not self.isVisible() or self.window().isMinimized():
            return 250
        idle_interval, active_interval = {
            "economy": (83, 50),
            "balanced": (62, 33),
            "high": (42, 17),
        }.get(self.settings.quality, (62, 33))
        if self.settings.reduced_motion:
            return min(160, idle_interval * 2) if self._state in {
                AssistantState.IDLE,
                AssistantState.DISABLED,
            } else min(100, active_interval * 2)
        if self._state is AssistantState.DISABLED:
            return 125
        if self._state is AssistantState.IDLE:
            return idle_interval
        return active_interval

    def _animate(self) -> None:
        now = time.perf_counter()
        dt = min(0.08, max(0.001, now - self._last_tick))
        self._last_tick = now
        desired_interval = self._target_interval()
        if self._timer.interval() != desired_interval:
            self._timer.setInterval(desired_interval)
        if not self.isVisible() or self.window().isMinimized():
            return

        transition = 1.0 - math.exp(-dt * 5.8)
        for state in AssistantState:
            target = 1.0 if state is self._state else 0.0
            self._state_mix[state] += (target - self._state_mix[state]) * transition
        self._features = self.analyzer.snapshot(self._state)
        self.update()

    def _update_fps(self) -> None:
        now = time.perf_counter()
        self._frames += 1
        elapsed = now - self._fps_window_started
        if elapsed < 1.0:
            return
        self._fps = self._frames / elapsed
        self._frames = 0
        self._fps_window_started = now

    def paintGL(self) -> None:
        self._update_fps()
        if self._software_fallback or self._program is None or self._functions is None:
            self._paint_software_gold()
            return
        dpr = self.devicePixelRatioF()
        pixel_width = max(1, int(self.width() * dpr))
        pixel_height = max(1, int(self.height() * dpr))
        f = self._functions
        f.glViewport(0, 0, pixel_width, pixel_height)
        f.glClearColor(0.0, 0.0, 0.0, 0.0)
        f.glClear(GL_COLOR_BUFFER_BIT)

        program = self._program
        if not program.bind():
            return
        features = self._features
        program.setUniformValue("u_resolution", QVector2D(float(pixel_width), float(pixel_height)))
        program.setUniformValue("u_time", float(time.perf_counter() - self._start_time))
        program.setUniformValue("u_amplitude", float(features.current_amplitude))
        program.setUniformValue("u_smoothed_amplitude", float(features.smoothed_amplitude))
        program.setUniformValue("u_low_energy", float(features.low_frequency_energy))
        program.setUniformValue("u_mid_energy", float(features.mid_frequency_energy))
        program.setUniformValue("u_high_energy", float(features.high_frequency_energy))
        program.setUniformValue("u_peak_impulse", float(features.peak_impulse))
        program.setUniformValue("u_listening", float(self._state_mix[AssistantState.LISTENING]))
        program.setUniformValue("u_thinking", float(self._state_mix[AssistantState.THINKING]))
        program.setUniformValue("u_speaking", float(self._state_mix[AssistantState.SPEAKING]))
        program.setUniformValue("u_error", float(self._state_mix[AssistantState.ERROR]))
        program.setUniformValue("u_disabled", float(self._state_mix[AssistantState.DISABLED]))
        motion = self.settings.motion_intensity * (0.42 if self.settings.reduced_motion else 1.0)
        program.setUniformValue("u_motion", float(motion))
        program.setUniformValue("u_droplets", 1.0 if self.settings.droplets else 0.0)
        program.setUniformValue("u_opacity", float(self.settings.visibility))

        if self._vbo is not None:
            self._vbo.bind()
            program.enableAttributeArray(0)
            program.setAttributeBuffer(0, GL_FLOAT, 0, 2, 0)
            f.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
            program.disableAttributeArray(0)
            self._vbo.release()
        program.release()

    def _paint_software_gold(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setOpacity(self.settings.visibility)
        t = time.perf_counter() - self._start_time
        activity = self._features.smoothed_amplitude
        center = QPointF(self.width() * 0.5, self.height() * 0.5)
        extent = min(self.width(), self.height())
        core_radius = extent * (0.115 + activity * 0.025)

        halo = QRadialGradient(center, extent * 0.40)
        halo.setColorAt(0.0, QColor(255, 156, 24, 90 + int(activity * 70)))
        halo.setColorAt(0.42, QColor(160, 72, 4, 32))
        halo.setColorAt(1.0, QColor(20, 8, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, extent * 0.37, extent * 0.37)

        painter.setBrush(QColor(25, 10, 1, 25))
        painter.setPen(QPen(QColor(255, 146, 22, 190), max(1.0, extent * 0.005)))
        painter.drawEllipse(center, extent * 0.315, extent * 0.315)

        for index, scale in enumerate((0.35, 0.31, 0.265)):
            rect = QRectF(
                center.x() - extent * scale,
                center.y() - extent * scale,
                extent * scale * 2.0,
                extent * scale * 2.0,
            )
            width = max(1.0, extent * (0.007 - index * 0.0012))
            painter.setPen(QPen(QColor(255, 132 + index * 25, 18, 205), width))
            offset = int((t * (30.0 + index * 12.0) * (-1 if index == 1 else 1)) % 360)
            for segment in range(10 + index * 4):
                start = int((segment * (360 / (10 + index * 4)) + offset) * 16)
                painter.drawArc(rect, start, int((15 - index * 2) * 16))

        core = QRadialGradient(center, core_radius * 1.8)
        core.setColorAt(0.0, QColor(255, 250, 190, 248))
        core.setColorAt(0.19, QColor(255, 174, 30, 245))
        core.setColorAt(0.58, QColor(177, 74, 2, 215))
        core.setColorAt(1.0, QColor(34, 11, 0, 0))
        painter.setPen(QPen(QColor(255, 181, 43, 220), max(1.0, extent * 0.006)))
        painter.setBrush(core)
        painter.drawEllipse(center, core_radius, core_radius)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._window_origin = self.window().pos()
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_global is not None and self._window_origin is not None:
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() > 5:
                self._dragged = True
                self.window().move(self._window_origin + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            if not self._dragged:
                self.clicked.emit()
            self._press_global = None
            self._window_origin = None
            self._dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_requested.emit(event.globalPos())
        event.accept()
