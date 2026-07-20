from __future__ import annotations

import json
import os
import platform
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from .history import read_diagnostics, read_history
from .settings import BASE_DIR, get_config, get_secret


REPORT_DIR = BASE_DIR / "diagnostics"
MAX_ERROR_CHARS = 80_000
DEFAULT_SHARE_SECONDS = 10 * 60
_share_lock = threading.Lock()
_active_server: ThreadingHTTPServer | None = None


def _secret_values() -> list[str]:
    values: list[str] = []
    for key, value in get_config().items():
        if re.search(r"(?i)(api[_-]?key|token|password|secret|credential)", key):
            value = str(value or "").strip()
            if len(value) >= 4:
                values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact_diagnostic_text(text: str) -> str:
    """Remove credentials from arbitrary logs before they leave the Pi."""
    text = str(text or "")
    for value in _secret_values():
        text = text.replace(value, "[REDACTED]")
    patterns = (
        (r"AIza[0-9A-Za-z_-]{20,}", "[GOOGLE_API_KEY_REDACTED]"),
        (r"(?i)\bBearer\s+[0-9A-Za-z._~+\-/=]+", "Bearer [REDACTED]"),
        (
            r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|"
            r"client[_ -]?secret|credential)(\s*[:=]\s*)([^\s,;]+)",
            r"\1\2[REDACTED]",
        ),
        (r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@", r"\1[REDACTED]@"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def _technical_errors() -> str:
    return redact_diagnostic_text(read_diagnostics())[-MAX_ERROR_CHARS:]


def _conversation_context() -> str:
    # The action immediately preceding an error can be decisive. Keep the
    # available history but redact credentials before it leaves the Pi.
    return redact_diagnostic_text(read_history())


def _read_version() -> str:
    try:
        return (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "desconocida"


def _update_status() -> str:
    try:
        data = json.loads(
            (BASE_DIR / ".updates" / "state.json").read_text(encoding="utf-8")
        )
    except Exception:
        return "sin estado guardado"
    allowed = {
        key: data.get(key)
        for key in (
            "status",
            "previous_version",
            "installed_version",
            "restart_required",
            "installed_at",
            "restored_version",
        )
        if key in data
    }
    return json.dumps(allowed, ensure_ascii=False, sort_keys=True)


def _audio_summary() -> str:
    lines = [
        f"entrada_configurada={get_secret('INPUT_DEVICE') or 'predeterminada'}",
        f"salida_configurada={get_secret('OUTPUT_DEVICE') or 'predeterminada'}",
    ]
    try:
        import sounddevice as sd

        default_input, default_output = sd.default.device
        lines.append(f"entrada_predeterminada={default_input}")
        lines.append(f"salida_predeterminada={default_output}")
        lines.append(f"dispositivos_detectados={len(sd.query_devices())}")
    except Exception as exc:
        lines.append("consulta_audio_error=" + redact_diagnostic_text(str(exc))[:500])
    return "\n".join(lines)


def create_diagnostic_report(runtime_state: dict | None = None) -> Path:
    """Create a private diagnostic report with full redacted context."""
    now = time.strftime("%Y-%m-%d %H:%M:%S %z")
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(BASE_DIR))
    runtime_state = {
        str(key): value for key, value in (runtime_state or {}).items()
    }
    content = "\n".join(
        (
            "JARVIS · INFORME DE DIAGNÓSTICO SEGURO",
            "Incluye el contexto disponible. Claves, tokens y contraseñas están ocultos.",
            "",
            "[SISTEMA]",
            f"generado={now}",
            f"jarvis_version={_read_version()}",
            f"python={platform.python_version()}",
            f"ejecutable_python={sys.executable}",
            f"sistema={platform.platform()}",
            f"arquitectura={platform.machine()}",
            f"cpu_logicos={psutil.cpu_count()}",
            f"memoria_total_mb={memory.total // (1024 * 1024)}",
            f"memoria_disponible_mb={memory.available // (1024 * 1024)}",
            f"disco_libre_mb={disk.free // (1024 * 1024)}",
            f"wake_mode={get_secret('WAKE_MODE', 'wakeword')}",
            f"update={_update_status()}",
            "",
            "[ESTADO DE JARVIS]",
            redact_diagnostic_text(
                json.dumps(runtime_state, ensure_ascii=False, sort_keys=True, default=str)
            ),
            "",
            "[AUDIO]",
            _audio_summary(),
            "",
            "[CONVERSACIONES RECIENTES]",
            _conversation_context() or "No hay conversaciones registradas.",
            "",
            "[ERRORES TÉCNICOS RECIENTES]",
            _technical_errors() or "No hay errores técnicos registrados.",
            "",
        )
    )
    content = redact_diagnostic_text(content)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(REPORT_DIR, 0o700)
    except OSError:
        pass
    target = REPORT_DIR / time.strftime("jarvis-diagnostico-%Y%m%d-%H%M%S.txt")
    target.write_text(content, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def _local_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        address = sock.getsockname()[0]
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    finally:
        sock.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def stop_diagnostic_share() -> None:
    global _active_server
    with _share_lock:
        server, _active_server = _active_server, None
    if server is not None:
        server.shutdown()
        server.server_close()


def share_diagnostic_report(
    report: Path,
    expires_seconds: int = DEFAULT_SHARE_SECONDS,
) -> dict:
    """Expose one report temporarily over the LAN behind a random URL."""
    global _active_server
    report = Path(report).resolve()
    payload = report.read_bytes()
    token = secrets.token_urlsafe(24)
    route = f"/jarvis-diagnostico/{token}"
    filename = report.name

    class ReportHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != route:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *_args):
            return

    stop_diagnostic_share()
    server = ThreadingHTTPServer(("0.0.0.0", 0), ReportHandler)
    server.daemon_threads = True
    with _share_lock:
        _active_server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    timer = threading.Timer(max(1, int(expires_seconds)), stop_diagnostic_share)
    timer.daemon = True
    timer.start()
    port = int(server.server_address[1])
    return {
        "path": str(report),
        "url": f"http://{_local_ipv4()}:{port}{route}",
        "expires_seconds": max(1, int(expires_seconds)),
    }
