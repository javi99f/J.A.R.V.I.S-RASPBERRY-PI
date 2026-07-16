from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
from omar_ai_core.settings import BASE_DIR, is_configured, is_desktop_mode, write_env

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QDesktopServices, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar,
)
from PyQt6.QtQuickWidgets import QQuickWidget

CONFIG_DIR = BASE_DIR / "config"
BRIGHTNESS_FILE = CONFIG_DIR / "brightness_state.json"

_DEFAULT_W, _DEFAULT_H = 800, 1200
_MIN_W,     _MIN_H     = 480, 720
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


def _run_hidden(command: list[str], **kwargs):
    """Run a metrics helper without flashing a console window on Windows."""
    if _OS == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(command, **kwargs)


class C:
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = _run_hidden(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = _run_hidden(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = _run_hidden(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS - powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = _run_hidden(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = _run_hidden(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = _run_hidden(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._anim_last  = time.perf_counter()
        self._fps_last   = self._anim_last
        self._fps_frames = 0
        self._fps_actual = 0.0
        self._blink_elapsed = 0.0
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._background_cache: QPixmap | None = None
        self._background_cache_size = QSize()
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        self._tmr.timeout.connect(self._step)
        # 8 ms targets the 120 Hz class (125 timer ticks/s on integer-ms Qt timers).
        self._tmr.start(8)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        frame_now = time.perf_counter()
        dt = min(0.1, max(0.001, frame_now - self._anim_last))
        self._anim_last = frame_now

        # Animation speed is time-based, independent from the achieved frame rate.
        self._tick += 20.0 * dt
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.06, 1.14)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        ref_alpha = 0.15 if self.speaking else 0.055
        sp = 1.0 - (1.0 - ref_alpha) ** (dt * 20.0)
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        speeds = [13.0, -9.0, 20.0] if self.speaking else [5.5, -3.5, 9.0]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd * dt) % 360

        self._scan  = (self._scan  + (30.0 if self.speaking else 13.0) * dt) % 360
        self._scan2 = (self._scan2 + (-20.0 if self.speaking else -7.5) * dt) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = (42.0 if self.speaking else 20.0) * dt
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        pulse_rate = 0.7 if self.speaking else 0.25
        if len(self._pulses) < 3 and random.random() < 1.0 - math.exp(-pulse_rate * dt):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 1.0 - math.exp(-2.8 * dt):
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2]*20*dt, p[1]+p[3]*20*dt,
             p[2]*(0.97**(dt*10)), p[3]*(0.97**(dt*10)), p[4]-0.28*dt]
            for p in self._particles if p[4] > 0
        ]

        self._blink_elapsed += dt
        if self._blink_elapsed >= 3.8:
            self._blink = not self._blink
            self._blink_elapsed = 0.0
        self.update()

    def paintEvent(self, _):
        frame_now = time.perf_counter()
        self._fps_frames += 1
        fps_span = frame_now - self._fps_last
        if fps_span >= 1.0:
            self._fps_actual = self._fps_frames / fps_span
            self._fps_frames = 0
            self._fps_last = frame_now

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots
        p.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)

        r_face = fw * 0.31

        # halo glow
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.MUTED_C if self.muted else C.PRI, a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # scanners
        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        # crosshair
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets
        bl = 24
        bc = qcol(C.PRI, 210)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # face
        if self._face_px:
            fsz    = int(fw * 0.62 * self._scale)
            scaled = self._face_px.scaled(
                fsz, fsz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(int(cx - fsz / 2), int(cy - fsz / 2), scaled)
        else:
            orb_r = int(fw * 0.27 * self._scale)
            oc    = (200, 0, 50) if self.muted else (0, 60, 110)
            for i in range(8, 0, -1):
                r2  = int(orb_r * i / 8)
                frc = i / 8
                a   = max(0, min(255, int(self._halo * 1.1 * frc)))
                p.setBrush(QBrush(QColor(int(oc[0]*frc), int(oc[1]*frc), int(oc[2]*frc), a)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))
            p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2))), 1))
            p.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
            p.drawText(QRectF(cx - 80, cy - 14, 160, 28),
                       Qt.AlignmentFlag.AlignCenter, "J.A.R.V.I.S")

        # particles
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # status text
        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "[X]  MUTED", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "[O]  SPEAKING", qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "*" if self._blink else "."
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "*" if self._blink else "."
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "*" if self._blink else "."
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        elif self.state == "STANDBY":
            sym = "*" if self._blink else "."
            txt, col = f"{sym}  SAY HEY JARVIS", qcol(C.PRI)
        else:
            sym = "*" if self._blink else "."
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            elif self.speaking:
                hgt = random.randint(3, 20)
                cl  = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
            else:
                hgt = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                cl  = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

class JarvisOrbitCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(360, 560)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.muted = False
        self.speaking = False
        self.state = "INITIALISING"
        self.metrics = {"cpu": 0.0, "mem": 0.0, "net": 0.0, "gpu": -1.0, "tmp": -1.0}
        self._tick = 0
        self._halo = 90.0
        self._target_halo = 90.0
        self._scale = 1.0
        self._target_scale = 1.0
        self._last_shift = time.time()
        self._anim_last = time.perf_counter()
        self._fps_last = self._anim_last
        self._fps_frames = 0
        self._fps_actual = 0.0
        self._scan = 0.0
        self._scan2 = 180.0
        self._rings = [0.0, 120.0, 240.0, 48.0]
        self._pulses = [0.0, 90.0, 180.0]
        self._particles: list[list[float]] = []
        self._field = [
            [random.random(), random.random(), random.uniform(0.15, 0.8), random.uniform(0.25, 1.0)]
            for _ in range(20)
        ]
        self._tmr = QTimer(self)
        self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(8)

    def resizeEvent(self, event):
        self._background_cache = None
        self._background_cache_size = QSize()
        super().resizeEvent(event)

    def _background_layer(self, W: int, H: int, cx: float, cy: float, orbit_r: float) -> QPixmap:
        size = QSize(W, H)
        if self._background_cache is not None and self._background_cache_size == size:
            return self._background_cache

        layer = QPixmap(size)
        layer.fill(qcol(C.BG))
        bgp = QPainter(layer)

        bg = QLinearGradient(0, 0, 0, H)
        bg.setColorAt(0.0, qcol("#000307"))
        bg.setColorAt(0.5, qcol("#000910"))
        bg.setColorAt(1.0, qcol("#000204"))
        bgp.fillRect(QRectF(0, 0, W, H), QBrush(bg))

        radial = QRadialGradient(QPointF(cx, cy), orbit_r * 1.45)
        radial.setColorAt(0.0, qcol(C.PRI, 42))
        radial.setColorAt(0.48, qcol("#002033", 22))
        radial.setColorAt(1.0, qcol(C.BG, 0))
        bgp.setPen(Qt.PenStyle.NoPen)
        bgp.setBrush(QBrush(radial))
        bgp.drawEllipse(QRectF(cx - orbit_r * 1.45, cy - orbit_r * 1.45,
                               orbit_r * 2.9, orbit_r * 2.9))

        bgp.setPen(QPen(qcol(C.PRI_GHO, 46), 1))
        grid = 18
        for x in range(0, W, grid):
            bgp.drawLine(QPointF(x, 0), QPointF(x, H))
        for y in range(0, H, grid):
            bgp.drawLine(QPointF(0, y), QPointF(W, y))
        bgp.end()

        self._background_cache = layer
        self._background_cache_size = size
        return layer

    def _step(self):
        frame_now = time.perf_counter()
        dt = min(0.1, max(0.001, frame_now - self._anim_last))
        self._anim_last = frame_now
        activity_hz = 6.25 if self.speaking else (1.0 / 0.7)
        self._tick += activity_hz * dt
        now = time.time()
        if now - self._last_shift > (0.12 if self.speaking else 0.55):
            if self.speaking:
                self._target_halo = random.uniform(155, 215)
                self._target_scale = random.uniform(1.035, 1.075)
            elif self.muted:
                self._target_halo = random.uniform(25, 45)
                self._target_scale = random.uniform(0.995, 1.002)
            else:
                self._target_halo = random.uniform(78, 110)
                self._target_scale = random.uniform(1.0, 1.012)
            self._last_shift = now

        follow_ref = 0.36 if self.speaking else 0.14
        follow = 1.0 - (1.0 - follow_ref) ** (activity_hz * dt)
        self._halo += (self._target_halo - self._halo) * follow
        self._scale += (self._target_scale - self._scale) * follow
        speeds = [1.75, -1.1, 2.45, -0.65] if self.speaking else [0.62, -0.4, 1.02, -0.22]
        for i, speed in enumerate(speeds):
            self._rings[i] = (self._rings[i] + speed * activity_hz * dt) % 360
        self._scan = (self._scan + (3.4 if self.speaking else 1.35) * activity_hz * dt) % 360
        self._scan2 = (self._scan2 + (-2.25 if self.speaking else -0.8) * activity_hz * dt) % 360

        limit = min(self.width(), self.height()) * 0.58
        pulse_speed = (4.8 if self.speaking else 1.9) * activity_hz * dt
        self._pulses = [r + pulse_speed for r in self._pulses if r + pulse_speed < limit]
        pulse_spawn_rate = 0.375 if self.speaking else 0.0086
        if len(self._pulses) < 3 and random.random() < 1.0 - math.exp(-pulse_spawn_rate * dt):
            self._pulses.append(0.0)

        for dot in self._field:
            dot[1] += dot[2] * 0.0009 * activity_hz * dt
            if dot[1] > 1.0:
                dot[0] = random.random()
                dot[1] = 0.0
                dot[2] = random.uniform(0.15, 0.8)
                dot[3] = random.uniform(0.25, 1.0)

        particle_rate = 1.375 if self.speaking else 0.0143
        if random.random() < 1.0 - math.exp(-particle_rate * dt):
            cx, cy = self.width() / 2, self.height() / 2
            fw = min(self.width(), self.height())
            angle = random.uniform(0, math.tau)
            radius = fw * random.uniform(0.16, 0.34)
            self._particles.append([
                cx + math.cos(angle) * radius,
                cy + math.sin(angle) * radius,
                math.cos(angle) * random.uniform(0.7, 2.2),
                math.sin(angle) * random.uniform(0.7, 2.2) - 0.25,
                1.0,
            ])
        self._particles = [
            [pt[0] + pt[2] * activity_hz * dt,
             pt[1] + pt[3] * activity_hz * dt,
             pt[2] * (0.97 ** (activity_hz * dt)),
             pt[3] * (0.97 ** (activity_hz * dt)),
             pt[4] - 0.026 * activity_hz * dt]
            for pt in self._particles if pt[4] > 0
        ]
        self.update()

    def _state_text(self):
        if self.muted:
            return "MICROPHONE MUTED", qcol(C.MUTED_C)
        if self.speaking:
            return "VOICE SYNTHESIS ACTIVE", qcol(C.PRI)
        if self.state == "THINKING":
            return "COGNITIVE PROCESSING", qcol(C.ACC2)
        if self.state == "PROCESSING":
            return "TASK PROCESSING", qcol(C.ACC2)
        if self.state == "LISTENING":
            return "LISTENING FOR COMMAND", qcol(C.GREEN)
        if self.state == "STANDBY":
            return "LOCAL WAKE WORD ONLY", qcol(C.PRI)
        return self.state, qcol(C.PRI)

    def paintEvent(self, _):
        frame_now = time.perf_counter()
        self._fps_frames += 1
        fps_span = frame_now - self._fps_last
        if fps_span >= 1.0:
            self._fps_actual = self._fps_frames / fps_span
            self._fps_frames = 0
            self._fps_last = frame_now

        p = QPainter(self)
        # At high refresh rates the software antialiasing pass is the dominant
        # cost. Thin HUD strokes remain crisp at native desktop resolution.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H * 0.445
        fw = min(W, H)
        orbit_r = min(W * 0.365, H * 0.27)
        core_r = orbit_r * 0.32

        p.drawPixmap(0, 0, self._background_layer(W, H, cx, cy, orbit_r))

        for dot in self._field:
            p.setPen(QPen(qcol(C.PRI, int(28 + dot[3] * 70)), 1))
            p.drawPoint(QPointF(dot[0] * W, dot[1] * H))
        for i in range(8):
            radius = orbit_r * (1.14 - i * 0.032)
            alpha = max(0, min(255, int(self._halo * 0.078 * (1.0 - i / 12))))
            p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, alpha), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        for pulse in self._pulses:
            alpha = max(0, int(190 * (1.0 - pulse / (fw * 0.58))))
            p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, alpha), 1.1))
            p.drawEllipse(QRectF(cx - pulse, cy - pulse, pulse * 2, pulse * 2))

        seg_a = [(0, .22), (.38, .78), (1.16, .18), (1.54, .52), (2.45, .3), (3.1, .72), (4.35, .2), (5.05, .55)]
        seg_b = [(.05, .44), (.92, .16), (1.33, .62), (2.22, .22), (2.76, .72), (4.1, .42), (5.18, .33)]
        seg_c = [(0, .14), (.28, .12), (.54, .18), (.92, .16), (1.18, .12), (1.52, .2),
                 (1.95, .14), (2.25, .12), (2.65, .18), (3.1, .16), (3.45, .12),
                 (3.78, .2), (4.2, .14), (4.62, .16), (5.05, .2), (5.5, .14), (5.85, .16)]
        ring_specs = [
            (1.05, 2.3, seg_c, self._rings[0], C.TEXT),
            (0.94, 4.2, seg_a, -self._rings[1], C.PRI),
            (0.76, 2.5, seg_b, self._rings[2], C.PRI),
            (0.47, 1.5, seg_c, -self._rings[3], "#5af3ff"),
        ]
        for idx, (frac, width, segments, base_deg, color) in enumerate(ring_specs):
            radius = orbit_r * frac * self._scale
            alpha = max(0, min(255, int(self._halo * (1.0 - idx * 0.13))))
            p.setPen(QPen(qcol(C.MUTED_C if self.muted else color, alpha), width))
            rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            base = math.radians(base_deg)
            for start, length in segments:
                p.drawArc(rect, int((base + start) * 180 / math.pi * 16), int(length * 180 / math.pi * 16))

        scan_r = orbit_r * 1.03
        scan_rect = QRectF(cx - scan_r, cy - scan_r, scan_r * 2, scan_r * 2)
        scan_len = 82 if self.speaking else 52
        p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 1.5))), 2.5))
        p.drawArc(scan_rect, int(self._scan * 16), int(scan_len * 16))
        p.setPen(QPen(qcol(C.ACC, min(150, int(self._halo * 0.72))), 1.4))
        p.drawArc(scan_rect, int(self._scan2 * 16), int(scan_len * 16))

        p.setPen(QPen(qcol(C.PRI, 130), 1))
        for deg in range(0, 360, 8):
            rad = math.radians(deg)
            outer = orbit_r * 1.145
            inner = orbit_r * (1.075 if deg % 32 == 0 else 1.10)
            p.drawLine(
                QPointF(cx + outer * math.cos(rad), cy - outer * math.sin(rad)),
                QPointF(cx + inner * math.cos(rad), cy - inner * math.sin(rad)),
            )

        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.44)), 1))
        cross_outer, cross_gap = orbit_r * 1.15, orbit_r * 0.34
        p.drawLine(QPointF(cx - cross_outer, cy), QPointF(cx - cross_gap, cy))
        p.drawLine(QPointF(cx + cross_gap, cy), QPointF(cx + cross_outer, cy))
        p.drawLine(QPointF(cx, cy - cross_outer), QPointF(cx, cy - cross_gap))
        p.drawLine(QPointF(cx, cy + cross_gap), QPointF(cx, cy + cross_outer))

        p.setPen(QPen(qcol(C.PRI, 66), 1))
        for i in range(6):
            ang = math.radians((self._rings[0] * 0.42 + i * 60) % 360)
            x1 = cx + math.cos(ang) * orbit_r * 0.26
            y1 = cy + math.sin(ang) * orbit_r * 0.26
            x2 = cx + math.cos(ang) * orbit_r * 0.88
            y2 = cy + math.sin(ang) * orbit_r * 0.88
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.setBrush(QBrush(qcol(C.PRI, 160)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx + math.cos(ang) * orbit_r * 0.63,
                                  cy + math.sin(ang) * orbit_r * 0.63), 4, 4)
            p.setPen(QPen(qcol(C.PRI, 66), 1))

        for pt in self._particles:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, max(0, min(255, int(pt[4] * 255))))))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # Text benefits from smoothing; geometry above stays on the fast path.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setPen(QPen(qcol(C.WHITE, 245), 1))
        p.setFont(QFont("Courier New", max(36, int(W * 0.07)), QFont.Weight.Bold))
        p.drawText(QRectF(cx - orbit_r * 0.72, cy - 38, orbit_r * 1.44, 50), Qt.AlignmentFlag.AlignCenter, "JARVIS")

        state_text, state_col = self._state_text()
        p.setPen(QPen(state_col, 1))
        p.setFont(QFont("Courier New", max(11, int(W * 0.017)), QFont.Weight.Bold))
        p.drawText(QRectF(0, cy + 18, W, 24), Qt.AlignmentFlag.AlignCenter, state_text)

        wave_y = cy + orbit_r * 0.78
        bars = 32
        bar_w = 5
        start_x = (W - bars * bar_w) / 2
        for i in range(bars):
            if self.muted:
                height, color = 2, qcol(C.MUTED_C)
            elif self.speaking:
                height = random.randint(4, 32)
                color = qcol(C.PRI if height > 12 else C.PRI_DIM)
            else:
                height = int(5 + 28 * abs(math.sin(self._tick * 0.055 + i * 0.32)))
                color = qcol(C.TEXT if i % 5 == 0 else C.PRI, 190 if i % 5 else 230)
            p.fillRect(QRectF(start_x + i * bar_w, wave_y + 28 - height, 3, height), color)

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM, 170), 1))
        stats = [
            ("CPU", self.metrics.get("cpu", 0.0), -0.72, -0.70),
            ("MEM", self.metrics.get("mem", 0.0), 0.72, -0.70),
            ("NET", self.metrics.get("net", 0.0) * 10, -0.72, 0.70),
            ("TMP", self.metrics.get("tmp", -1.0), 0.72, 0.70),
        ]
        for name, value, sx, sy in stats:
            text = "N/A" if value < 0 else f"{value:.0f}"
            x = cx + sx * orbit_r
            y = cy + sy * orbit_r
            p.drawText(QRectF(x - 42, y - 10, 84, 20), Qt.AlignmentFlag.AlignCenter, f"{name} {text}")

        p.setPen(QPen(qcol(C.GREEN if self._fps_actual >= 90 else C.ACC2, 210), 1))
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.drawText(
            QRectF(W - 105, 8, 95, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"FPS {self._fps_actual:5.1f}",
        )
        p.setPen(QPen(qcol(C.TEXT_DIM, 180), 1))
        p.drawText(
            QRectF(W - 140, 25, 130, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "CPU RASTER",
        )


class JarvisQuickCanvas(QQuickWidget):
    """GPU-backed HUD integrated into the surrounding QWidget interface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 560)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.setClearColor(QColor(C.BG))
        self._muted = False
        self._speaking = False
        self._state = "INITIALISING"
        self._metrics = {"cpu": 0.0, "mem": 0.0, "net": 0.0, "gpu": -1.0, "tmp": -1.0}

        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        qml_path = bundle_root / "assets" / "jarvis_hud.qml"
        self.setSource(QUrl.fromLocalFile(str(qml_path)))
        if self.status() == QQuickWidget.Status.Error:
            details = "; ".join(error.toString() for error in self.errors())
            raise RuntimeError(f"Qt Quick HUD failed to load: {details}")
        if self.rootObject() is None:
            raise RuntimeError("Qt Quick HUD did not create a root object.")
        self._sync_all()

    def _set_qml(self, name: str, value) -> None:
        root = self.rootObject()
        if root is not None:
            root.setProperty(name, value)

    def _sync_all(self) -> None:
        self._set_qml("muted", self._muted)
        self._set_qml("speaking", self._speaking)
        self._set_qml("assistantState", self._state)
        self.metrics = self._metrics

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self._muted = bool(value)
        self._set_qml("muted", self._muted)

    @property
    def speaking(self) -> bool:
        return self._speaking

    @speaking.setter
    def speaking(self, value: bool) -> None:
        self._speaking = bool(value)
        self._set_qml("speaking", self._speaking)

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        self._state = str(value)
        self._set_qml("assistantState", self._state)

    @property
    def metrics(self) -> dict:
        return self._metrics

    @metrics.setter
    def metrics(self, values: dict) -> None:
        self._metrics = dict(values or {})
        self._set_qml("cpu", float(self._metrics.get("cpu", 0.0)))
        self._set_qml("memory", float(self._metrics.get("mem", 0.0)))
        self._set_qml("network", float(self._metrics.get("net", 0.0)) * 10.0)
        self._set_qml("temperature", float(self._metrics.get("tmp", -1.0)))


def create_jarvis_canvas():
    renderer = os.getenv("JARVIS_RENDERER", "qml" if is_desktop_mode() else "raster").lower()
    if renderer not in {"raster", "cpu"}:
        try:
            return JarvisQuickCanvas()
        except Exception as exc:
            print(f"[JARVIS] GPU HUD unavailable; using raster fallback: {exc}")
    return JarvisOrbitCanvas()


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0-100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image": ("IMG", "#00d4ff"), "video": ("VID", "#ff6b00"),
    "audio": ("AUD", "#cc44ff"), "pdf": ("PDF", "#ff4444"),
    "word": ("DOC", "#4488ff"), "excel": ("XLS", "#44bb44"),
    "code": ("CODE", "#ffcc00"), "archive": ("ZIP", "#ff8844"),
    "pptx": ("PPT", "#ff6622"), "text": ("TXT", "#aaaaaa"),
    "data": ("DATA", "#88ddff"), "unknown": ("FILE", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class GridHeader(QWidget):
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0.0, qcol("#00080d", 185))
        bg.setColorAt(0.72, qcol("#000509", 62))
        bg.setColorAt(1.0, qcol("#000509", 0))
        p.fillRect(self.rect(), QBrush(bg))

        glow = QRadialGradient(QPointF(self.width() / 2, self.height() * 1.18), self.width() * 0.72)
        glow.setColorAt(0.0, qcol(C.PRI, 30))
        glow.setColorAt(0.58, qcol(C.PRI, 5))
        glow.setColorAt(1.0, qcol(C.BG, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(-self.width() * 0.12, self.height() * 0.32, self.width() * 1.24, self.height() * 1.35))

        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(qcol(C.PRI_GHO, 58), 1))
        grid = 18
        for x in range(0, self.width(), grid):
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        for y in range(0, self.height(), grid):
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        line = QLinearGradient(24, self.height() - 1, self.width() - 24, self.height() - 1)
        line.setColorAt(0.0, qcol(C.PRI, 0))
        line.setColorAt(0.5, qcol(C.PRI, 82))
        line.setColorAt(1.0, qcol(C.PRI, 0))
        p.setPen(QPen(QBrush(line), 1))
        p.drawLine(QPointF(24, self.height() - 1), QPointF(self.width() - 24, self.height() - 1))


class DimOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._alpha = 0
        self.hide()

    def set_brightness(self, pct: int):
        pct = max(10, min(100, int(pct)))
        self._alpha = int((100 - pct) / 90 * 210)
        self.setVisible(self._alpha > 0)
        self.update()

    def paintEvent(self, _):
        if self._alpha <= 0:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, self._alpha))


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images - Video - Audio - PDF - Docs - Code - Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "v")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  -  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "..." + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "X")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("ACTIVAR JARVIS", 13, True))
        layout.addWidget(_lbl("Solo necesitas una clave gratuita de Google Gemini.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("CLAVE API DE GEMINI", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("Pega aqui tu clave de Gemini")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addWidget(_lbl(
            "Google ofrece un nivel gratuito con limites de uso.\n"
            "La clave OpenRouter no es necesaria.",
            8, color=C.TEXT_MED,
        ))

        get_key_btn = QPushButton("CONSEGUIR CLAVE GRATIS")
        get_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key_btn.setFixedHeight(32)
        get_key_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ border: 1px solid {C.PRI}; }}
        """)
        get_key_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://aistudio.google.com/apikey"))
        )
        layout.addWidget(get_key_btn)

        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        init_btn = QPushButton("GUARDAR Y ENTRAR")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)
    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, "", "desktop")


class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("JARVIS - Personal Assistant")
        screen = QApplication.primaryScreen().availableGeometry()
        self._compact_portrait = (
            not is_desktop_mode()
            and screen.width() <= 640
            and screen.height() <= 1100
        )
        if is_desktop_mode():
            # Keep the native title bar and its move/minimise/maximise/close
            # controls.  This is explicit so the desktop build can never
            # inherit kiosk-like window flags from the Raspberry Pi mode.
            self.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowSystemMenuHint
                | Qt.WindowType.WindowMinimizeButtonHint
                | Qt.WindowType.WindowMaximizeButtonHint
                | Qt.WindowType.WindowCloseButtonHint
            )

            # Size the desktop window from the *available* work area.  The
            # old code resized correctly but centred using 800x1200, which
            # moved the title bar above the top edge on shorter displays.
            target_h = min(920, max(360, screen.height() - 60))
            target_w = min(
                max(360, screen.width() - 40),
                max(420, int(target_h * 2 / 3)),
            )
            self.setMinimumSize(min(_MIN_W, target_w), min(_MIN_H, target_h))
            self.resize(target_w, target_h)
        else:
            # The 7-inch panel is physically 1024x600.  Once rotated it is a
            # real 600x1024 desktop, so size the kiosk to that geometry rather
            # than first laying it out as the old virtual 800x1200 window.
            self.setMinimumSize(
                min(_MIN_W, screen.width()),
                min(_MIN_H, screen.height()),
            )
            self.resize(screen.width(), screen.height())

        # Use the final window dimensions and include the work area's origin
        # so this also behaves correctly with multiple monitors.
        self.move(
            screen.x() + max(0, (screen.width() - self.width()) // 2),
            screen.y() + max(0, (screen.height() - self.height()) // 2),
        )

        self.on_text_command  = None
        self.on_manual_activate = None
        self._muted           = False
        self._current_file: str | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.hud = create_jarvis_canvas()
        root.addWidget(self.hud, stretch=1)

        self._log = LogWidget()
        self._log.hide()
        self._drop_zone = FileDropZone()
        self._drop_zone.hide()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        self._file_hint = QLabel("")
        self._input = QLineEdit()
        self._input.hide()
        self._mute_btn = QPushButton()
        self._mute_btn.hide()
        self._dim_overlay = DimOverlay(central)
        self._last_brightness_pct: int | None = None
        root.addWidget(self._build_quick_controls())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # System metrics update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._brightness_tmr = QTimer(self)
        self._brightness_tmr.timeout.connect(self._update_brightness_overlay)
        self._brightness_tmr.start(500)
        self._update_brightness_overlay()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            cw = self.centralWidget()
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if hasattr(self, "_dim_overlay"):
            self._dim_overlay.setGeometry(self.centralWidget().rect())
            self._dim_overlay.raise_()

    def _update_metrics(self):
        self.hud.metrics = _metrics.snapshot()
        self.hud.update()

    def _update_brightness_overlay(self):
        pct = 100
        try:
            with open(BRIGHTNESS_FILE, "r", encoding="utf-8") as f:
                pct = int(json.load(f).get("brightness", 100))
        except Exception:
            pct = 100
        pct = max(10, min(100, pct))
        if pct == self._last_brightness_pct:
            return
        self._last_brightness_pct = pct
        self._dim_overlay.setGeometry(self.centralWidget().rect())
        self._dim_overlay.set_brightness(pct)
        self._dim_overlay.raise_()

    def _build_header(self) -> QWidget:
        w = GridHeader()
        w.setFixedHeight(128 if self._compact_portrait else 150)
        w.setStyleSheet(f"""
            QWidget {{
                background: transparent;
                border: none;
            }}
        """)
        lay = QVBoxLayout(w)
        if self._compact_portrait:
            lay.setContentsMargins(14, 9, 14, 7)
        else:
            lay.setContentsMargins(18, 16, 18, 10)
        lay.setSpacing(2)

        title = QLabel("JARVIS")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Courier New", 15 if self._compact_portrait else 18, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none; letter-spacing: 0;")
        lay.addWidget(title)

        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._clock_lbl.setFont(QFont("Courier New", 46 if self._compact_portrait else 54, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        lay.addWidget(self._clock_lbl)

        self._date_lbl = QLabel("")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._date_lbl.setFont(QFont("Courier New", 10 if self._compact_portrait else 11, QFont.Weight.Bold))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        lay.addWidget(self._date_lbl)
        return w

    def _build_quick_controls(self) -> QWidget:
        panel = QWidget()
        panel.setFixedHeight(58)
        panel.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        row = QHBoxLayout(panel)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Escribe una orden...")
        self._input.setFixedHeight(42)
        self._input.returnPressed.connect(self._send)
        self._input.setStyleSheet(
            f"background: #000d12; color: {C.TEXT}; border: 1px solid {C.BORDER}; padding: 6px;"
        )
        row.addWidget(self._input, stretch=1)

        activate = QPushButton("ACTIVAR")
        activate.setFixedSize(92, 42)
        activate.clicked.connect(self._manual_activate)
        activate.setStyleSheet(
            f"background: {C.PRI_GHO}; color: {C.PRI}; border: 1px solid {C.PRI};"
        )
        row.addWidget(activate)

        update = QPushButton("UPDATE")
        update.setToolTip("Buscar actualizaciones oficiales de Jarvis")
        update.setFixedSize(68, 42)
        update.clicked.connect(self._check_updates)
        update.setStyleSheet(
            f"background: #001019; color: {C.TEXT_MED}; border: 1px solid {C.BORDER};"
        )
        row.addWidget(update)

        self._mute_btn = QPushButton("MIC")
        self._mute_btn.setFixedSize(72, 42)
        self._mute_btn.clicked.connect(self._toggle_mute)
        row.addWidget(self._mute_btn)
        self._style_mute_btn()
        return panel

    def _manual_activate(self):
        if self.on_manual_activate:
            self.on_manual_activate()

    def _check_updates(self):
        self._log.append_log("SYS: Checking for JARVIS updates...")
        if self.on_text_command:
            threading.Thread(
                target=self.on_text_command,
                args=("Busca actualizaciones de Jarvis",),
                daemon=True,
            ).start()

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%I:%M %p").lstrip("0"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)

        hdr = QLabel("SYS MONITOR")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP 00:00:00")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC 000")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",     C.GREEN),
            ("SEC\nCLEARED",        C.PRI),
            ("PRIVATE\nCORE",       C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-left: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"> {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded - drop or click above to upload")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._mute_btn = QPushButton("MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        fs_btn = QPushButton("FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont("Courier New", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{
                color: {C.PRI}; border: 1px solid {C.BORDER_B};
            }}
        """)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        return w

    def _build_command_bar(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(82)
        w.setStyleSheet(f"""
            QWidget {{
                background: rgba(0, 12, 18, 210);
                border-top: 1px solid {C.BORDER_A};
            }}
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)

        attach = QPushButton("FILE")
        attach.setFixedSize(62, 42)
        attach.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setStyleSheet(f"""
            QPushButton {{
                background: #000d14; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{
                color: {C.PRI}; border: 1px solid {C.PRI};
                background: {C.PRI_GHO};
            }}
        """)
        attach.clicked.connect(self._choose_file)
        lay.addWidget(attach)

        lay.addLayout(self._build_input_row(), stretch=1)

        self._mute_btn = QPushButton("MIC")
        self._mute_btn.setFixedSize(72, 42)
        self._mute_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        fs_btn = QPushButton("FULL")
        fs_btn.setFixedSize(72, 42)
        fs_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(f"""
            QPushButton {{
                background: #000d14; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{
                color: {C.PRI}; border: 1px solid {C.PRI};
                background: {C.PRI_GHO};
            }}
        """)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)
        return w

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Command JARVIS...")
        self._input.setFont(QFont("Courier New", 13))
        self._input.setFixedHeight(42)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 4px 12px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton(">")
        send.setFixedSize(48, 42)
        send.setFont(QFont("Courier New", 16, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load file for JARVIS")
        if path:
            self._on_file_selected(path)

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(f"> {txt}")
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  -  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl("Personal AI Lab  -  Private Assistant Core"))
        lay.addStretch()
        lay.addWidget(_fl("LOCAL PI SYSTEM", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  -  {size}  -  Tell JARVIS what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 4px;
                }}
            """)
        else:
            self._mute_btn.setText("MIC")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 4px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _check_config(self) -> bool:
        return is_configured()

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 430
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    # Change signature:
    def _on_setup_done(self, key: str, or_key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        write_env(key, or_key)
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None
        self._apply_state("STANDBY")
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. JARVIS online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        if is_desktop_mode():
            self._win.show()
        else:
            self._win.showFullScreen()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._current_file

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_manual_activate(self):
        return self._win.on_manual_activate

    @on_manual_activate.setter
    def on_manual_activate(self, cb):
        self._win.on_manual_activate = cb

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")


# Runtime keeps importing JarvisUI from this module.  Select the new native
# liquid interface only in desktop mode and preserve the Raspberry Pi HUD.
if is_desktop_mode():
    from .liquid_window import LiquidJarvisUI as JarvisUI
