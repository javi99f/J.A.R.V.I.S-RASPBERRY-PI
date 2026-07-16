"""One-shot, secret-safe diagnostics for Gemini Live on Raspberry Pi.

This script deliberately tests each layer separately: configuration, clock,
DNS, IPv4/IPv6 TLS, REST authentication, model visibility, a raw Live API
WebSocket, and finally the Google Gen AI SDK with both minimal and Jarvis
configuration.  The API key is never printed and every exception is scrubbed
before it is written to the report.
"""

from __future__ import annotations

import asyncio
import email.utils
import http.client
import importlib.metadata
import json
import os
import re
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
REPORT_FILE = ROOT / "jarvis-live-diagnostic.txt"
HOST = "generativelanguage.googleapis.com"
PORT = 443
MODEL = "gemini-3.1-flash-live-preview"
REST_TIMEOUT = 10.0
WS_OPEN_TIMEOUT = 18.0
WS_MESSAGE_TIMEOUT = 18.0


@dataclass
class Result:
    name: str
    state: str
    detail: str
    category: str = ""


RESULTS: list[Result] = []
DNS_ADDRESSES: dict[int, list[tuple[Any, ...]]] = {
    socket.AF_INET: [],
    socket.AF_INET6: [],
}
API_KEY = ""


def scrub(value: Any) -> str:
    """Remove credentials and keep diagnostics to one readable line."""
    text = str(value or "")
    if API_KEY:
        text = text.replace(API_KEY, "<redacted>")
    text = re.sub(r"(?i)([?&]key=)[^&\s'\"]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(x-goog-api-key\s*[:=]\s*)\S+", r"\1<redacted>", text)
    text = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "<redacted>", text)
    text = re.sub(r"\bAQ\.[0-9A-Za-z._-]{20,}\b", "<redacted>", text)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return text[:360] or "sin detalle"


def add(name: str, state: str, detail: Any, category: str = "") -> None:
    result = Result(name, state, scrub(detail), category)
    RESULTS.append(result)
    print(f"[{state:<4}] {name:<18} {result.detail}", flush=True)


def parse_env(path: Path) -> tuple[dict[str, str], dict[str, int]]:
    values: dict[str, str] = {}
    counts: dict[str, int] = {}
    if not path.exists():
        return values, counts
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
        counts[key] = counts.get(key, 0) + 1
    return values, counts


def load_configuration() -> bool:
    global API_KEY
    values, counts = parse_env(ENV_FILE)
    file_key = values.get("GEMINI_API_KEY", "")
    process_key = os.environ.get("GEMINI_API_KEY", "").strip()
    API_KEY = process_key or file_key

    if not API_KEY:
        add("CONFIG", "FAIL", "GEMINI_API_KEY no existe", "auth")
        return False
    key_lines = counts.get("GEMINI_API_KEY", 0)
    if not process_key and key_lines != 1:
        add(
            "CONFIG",
            "FAIL",
            f"hay {key_lines} lineas GEMINI_API_KEY en .env",
            "auth",
        )
        return False
    source = "entorno del proceso" if process_key else ".env"
    if process_key and key_lines != 1:
        add(
            "CONFIG",
            "WARN",
            f"la clave viene del proceso; .env contiene {key_lines} lineas de clave",
            "auth",
        )
    elif process_key and file_key and process_key != file_key:
        add("CONFIG", "WARN", "una variable del proceso sustituye la clave de .env", "auth")
    elif API_KEY != API_KEY.strip() or any(ch.isspace() for ch in API_KEY):
        add("CONFIG", "FAIL", "la clave contiene espacios", "auth")
        return False
    else:
        add("CONFIG", "OK", f"clave cargada desde {source}; formato no expuesto")
    return True


def probe_environment() -> None:
    now = datetime.now(timezone.utc)
    add("RELOJ", "OK", f"UTC local {now.strftime('%Y-%m-%d %H:%M:%S')}")
    proxy_names = [
        name
        for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
        if os.environ.get(name)
    ]
    if proxy_names:
        add("PROXY", "WARN", "variables activas: " + ",".join(proxy_names), "proxy")
    else:
        add("PROXY", "OK", "sin proxy configurado")
    versions = []
    for package in ("google-genai", "websockets"):
        try:
            versions.append(f"{package}={importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            versions.append(f"{package}=NO_INSTALADO")
    add("VERSIONES", "OK", "; ".join(versions))
    competing = 0
    proc_root = Path("/proc")
    if proc_root.exists():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                command = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
            except (OSError, PermissionError):
                continue
            if b"omar_ai_core" in command:
                competing += 1
    if competing:
        add(
            "JARVIS_ACTIVO",
            "WARN",
            f"hay {competing} proceso(s) de Jarvis reintentando en paralelo; puede alterar tiempos",
        )
    else:
        add("JARVIS_ACTIVO", "OK", "sin otro proceso de Jarvis")


def probe_dns() -> None:
    try:
        infos = socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)
    except Exception as exc:
        add("DNS", "FAIL", f"{type(exc).__name__}: {exc}", "dns")
        return
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family in DNS_ADDRESSES and sockaddr not in DNS_ADDRESSES[family]:
            DNS_ADDRESSES[family].append(sockaddr)
    v4 = len(DNS_ADDRESSES[socket.AF_INET])
    v6 = len(DNS_ADDRESSES[socket.AF_INET6])
    if v4 or v6:
        add("DNS", "OK", f"IPv4={v4}; IPv6={v6}")
    else:
        add("DNS", "FAIL", "sin direcciones IPv4 ni IPv6", "dns")


def open_tls(family: int, timeout: float = REST_TIMEOUT) -> ssl.SSLSocket:
    addresses = DNS_ADDRESSES.get(family, [])
    if not addresses:
        raise OSError("DNS no devolvio direcciones para esta familia")
    errors: list[str] = []
    deadline = time.monotonic() + timeout
    for sockaddr in addresses[:3]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        raw = socket.socket(family, socket.SOCK_STREAM)
        raw.settimeout(max(0.2, remaining))
        try:
            raw.connect(sockaddr)
            context = ssl.create_default_context()
            tls = context.wrap_socket(raw, server_hostname=HOST)
            tls.settimeout(timeout)
            return tls
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            raw.close()
    raise OSError(" | ".join(errors))


def probe_tls(family: int, label: str) -> bool:
    if not DNS_ADDRESSES.get(family):
        add(label, "SKIP", "DNS no anuncio esta familia")
        return False
    started = time.monotonic()
    try:
        tls = open_tls(family)
        cert = tls.getpeercert()
        tls_version = tls.version() or "TLS"
        tls.close()
        elapsed = int((time.monotonic() - started) * 1000)
        expires = cert.get("notAfter", "fecha desconocida")
        add(label, "OK", f"{tls_version}; {elapsed} ms; certificado hasta {expires}")
        return True
    except Exception as exc:
        category = "tls" if isinstance(exc, ssl.SSLError) else "route"
        add(label, "FAIL", f"{type(exc).__name__}: {exc}", category)
        return False


def direct_https_get(path: str, family: int) -> tuple[int, dict[str, str], bytes]:
    tls = open_tls(family)
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        "User-Agent: Jarvis-Pi-Diagnostic/1\r\n"
        "Accept: application/json\r\n"
        f"x-goog-api-key: {API_KEY}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    tls.sendall(request)
    response = http.client.HTTPResponse(tls)
    response.begin()
    body = response.read(256 * 1024)
    headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    tls.close()
    return status, headers, body


def decode_error(body: bytes) -> tuple[str, str]:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return "", scrub(body[:300])
    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return "", scrub(error)
    status = str(error.get("status", ""))
    message = str(error.get("message", ""))
    details = error.get("details", [])
    reasons: list[str] = []
    if isinstance(details, list):
        for item in details:
            if isinstance(item, dict):
                reason = item.get("reason")
                if reason:
                    reasons.append(str(reason))
                metadata = item.get("metadata")
                if isinstance(metadata, dict) and metadata.get("reason"):
                    reasons.append(str(metadata["reason"]))
    code = "/".join(filter(None, [status, *reasons]))
    return code, message


def classify_error(status: int | None, code: str, message: str) -> str:
    text = f"{status or ''} {code} {message}".lower()
    if status == 401 or any(word in text for word in ("unauthenticated", "invalid api key", "invalid authentication", "credential")):
        return "auth"
    if status == 404 or ("model" in text and "not found" in text):
        return "model"
    if "billing" in text or "paid tier" in text or "payment" in text:
        return "billing"
    if status == 429 or any(word in text for word in ("quota", "resource_exhausted", "rate limit")):
        return "quota"
    if status == 403 or any(word in text for word in ("permission_denied", "service_disabled", "api_key_service_blocked")):
        return "permission"
    if status == 400 or "invalid_argument" in text:
        return "config"
    return "unknown"


def probe_rest(family: int) -> tuple[bool, bool]:
    auth_ok = False
    model_ok = False
    try:
        status, headers, body = direct_https_get("/v1beta/models?pageSize=1", family)
        date_header = headers.get("date")
        if date_header:
            try:
                server_dt = email.utils.parsedate_to_datetime(date_header)
                skew = abs((datetime.now(timezone.utc) - server_dt).total_seconds())
                add("RELOJ_SERVIDOR", "OK" if skew < 300 else "WARN", f"desfase {int(skew)} s", "clock" if skew >= 300 else "")
            except Exception:
                pass
        if status == 200:
            auth_ok = True
            add("AUTH_HTTPS", "OK", "HTTP 200; la clave autentica en Gemini")
        else:
            code, message = decode_error(body)
            category = classify_error(status, code, message)
            add("AUTH_HTTPS", "FAIL", f"HTTP {status} {code}: {message}", category)
            return auth_ok, model_ok
    except Exception as exc:
        add("AUTH_HTTPS", "FAIL", f"{type(exc).__name__}: {exc}", "https")
        return auth_ok, model_ok

    try:
        status, _headers, body = direct_https_get(f"/v1beta/models/{MODEL}", family)
        if status == 200:
            model_ok = True
            add("MODELO", "OK", f"HTTP 200; {MODEL} visible para el proyecto")
        else:
            code, message = decode_error(body)
            category = classify_error(status, code, message)
            add("MODELO", "FAIL", f"HTTP {status} {code}: {message}", category)
    except Exception as exc:
        add("MODELO", "FAIL", f"{type(exc).__name__}: {exc}", "https")
    return auth_ok, model_ok


def websocket_error(exc: BaseException) -> tuple[str, str]:
    status: int | None = None
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        body = getattr(response, "body", b"") or b""
        if isinstance(body, str):
            body = body.encode("utf-8", errors="replace")
        if body:
            code, message = decode_error(body)
            category = classify_error(status, code, message)
            return category, scrub(
                f"{type(exc).__name__}: HTTP {status} {code}: {message}"
            )
    text = scrub(f"{type(exc).__name__}: {exc}")
    return classify_error(status, "", text), text


async def probe_raw_websocket(
    family: int, label: str, generate: bool, auth_mode: str = "query"
) -> bool:
    try:
        from websockets.asyncio.client import connect
    except Exception as exc:
        add(label, "FAIL", f"websockets no disponible: {exc}", "sdk")
        return False

    uri = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    )
    additional_headers = None
    if auth_mode == "query":
        uri += f"?key={quote(API_KEY, safe='')}"
    elif auth_mode == "header":
        additional_headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": API_KEY,
        }
    else:
        raise ValueError(f"modo de autenticacion desconocido: {auth_mode}")
    setup = {
        "setup": {
            "model": f"models/{MODEL}",
            "generationConfig": {"responseModalities": ["AUDIO"]},
            "systemInstruction": {"parts": [{"text": "Diagnostico tecnico breve."}]},
        }
    }
    kwargs: dict[str, Any] = {
        "open_timeout": WS_OPEN_TIMEOUT,
        "close_timeout": 2,
        "ping_interval": None,
        "proxy": None,
        "family": family,
    }
    if additional_headers:
        kwargs["additional_headers"] = additional_headers
    started = time.monotonic()
    try:
        async with connect(uri, **kwargs) as websocket:
            handshake_ms = int((time.monotonic() - started) * 1000)
            await websocket.send(json.dumps(setup))
            raw = await asyncio.wait_for(websocket.recv(), timeout=WS_MESSAGE_TIMEOUT)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            payload = json.loads(raw)
            if "error" in payload:
                body = json.dumps(payload).encode()
                code, message = decode_error(body)
                category = classify_error(None, code, message)
                add(label, "FAIL", f"conexion {handshake_ms} ms; {code}: {message}", category)
                return False
            if "setupComplete" not in payload and "setup_complete" not in payload:
                add(label, "FAIL", f"respuesta de configuracion inesperada: {list(payload)[:4]}", "websocket")
                return False
            if not generate:
                add(label, "OK", f"WebSocket y setup aceptados en {handshake_ms} ms")
                return True

            await websocket.send(json.dumps({"realtimeInput": {"text": "Responde solamente OK."}}))
            deadline = time.monotonic() + WS_MESSAGE_TIMEOUT
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(
                    websocket.recv(), timeout=max(0.1, deadline - time.monotonic())
                )
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                payload = json.loads(raw)
                if "error" in payload:
                    code, message = decode_error(json.dumps(payload).encode())
                    category = classify_error(None, code, message)
                    add(label, "FAIL", f"setup OK; inferencia rechazada {code}: {message}", category)
                    return False
                if any(key in payload for key in ("serverContent", "server_content", "toolCall", "tool_call")):
                    add(label, "OK", f"WebSocket, modelo e inferencia OK; apertura {handshake_ms} ms")
                    return True
            raise TimeoutError("setup aceptado, pero no llego respuesta de inferencia")
    except Exception as exc:
        category, detail = websocket_error(exc)
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            category = "websocket_timeout"
        add(label, "FAIL", detail, category)
        return False


async def _sdk_connect(config: Any) -> None:
    from google import genai

    client = genai.Client(api_key=API_KEY, http_options={"api_version": "v1beta"})
    async with client.aio.live.connect(model=MODEL, config=config):
        return


async def probe_sdk(label: str, config: Any, force_ipv4: bool) -> bool:
    original = None
    live_module = None
    try:
        if force_ipv4:
            import google.genai.live as live_module

            original = live_module.ws_connect

            def direct_ipv4(*args: Any, **kwargs: Any) -> Any:
                kwargs.setdefault("open_timeout", WS_OPEN_TIMEOUT)
                kwargs.setdefault("close_timeout", 2)
                kwargs.setdefault("proxy", None)
                kwargs.setdefault("family", socket.AF_INET)
                return original(*args, **kwargs)

            live_module.ws_connect = direct_ipv4
        await asyncio.wait_for(_sdk_connect(config), timeout=WS_OPEN_TIMEOUT + 8)
        add(label, "OK", "SDK conecto y recibio setupComplete")
        return True
    except Exception as exc:
        category, detail = websocket_error(exc)
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            category = "websocket_timeout"
        add(label, "FAIL", detail, category)
        return False
    finally:
        if live_module is not None and original is not None:
            live_module.ws_connect = original


def build_jarvis_config() -> Any:
    from omar_ai_core.runtime import JarvisLive

    return JarvisLive.__new__(JarvisLive)._build_config()


def diagnosis() -> str:
    failed = [result for result in RESULTS if result.state == "FAIL"]
    categories = {result.category for result in failed if result.category}
    states = {result.name: result.state for result in RESULTS}
    if not failed:
        return "Todas las capas funcionan; si la interfaz falla, revisar audio/tareas posteriores a la conexion."
    # Differential probes are more precise than generic API categories.  For
    # example, a valid key may work in the official query-string flow while
    # the same key is rejected only in the SDK's header flow.
    if states.get("WS_RAW_IPV4") == "OK" and states.get("SDK_MIN_IPV4") == "FAIL":
        if states.get("WS_RAW_HEADER") == "FAIL":
            return "El WebSocket oficial con clave en URL funciona, pero la autenticacion por cabecera usada por el SDK falla."
        return "Gemini Live funciona en WebSocket directo; el fallo esta en el SDK/transporte Python."
    if states.get("WS_RAW_IPV4") == "OK" and states.get("SDK_JARVIS") == "FAIL":
        return "El transporte y el modelo funcionan; la configuracion completa de Jarvis es rechazada."
    # A real Live inference is more authoritative than the REST metadata
    # surface for a preview model. If every Live layer works, don't let a
    # metadata-only 404 produce a false model diagnosis.
    if (
        states.get("WS_RAW_IPV4") == "OK"
        and states.get("SDK_MIN_IPV4") == "OK"
        and states.get("SDK_JARVIS") == "OK"
    ):
        return "Gemini Live, el SDK y la configuracion completa de Jarvis funcionan; revisar solo tareas posteriores como audio."
    priority = (
        ("auth", "La autenticacion falla."),
        ("billing", "Gemini rechazo el proyecto por facturacion/plan."),
        ("quota", "Gemini rechazo la inferencia por cuota o limite de uso."),
        ("model", "La clave funciona, pero el modelo Live no esta disponible para este proyecto."),
        ("dns", "La Raspberry no resuelve el dominio de Gemini."),
        ("tls", "DNS funciona, pero falla TLS/certificados/reloj."),
    )
    for category, message in priority:
        if category in categories:
            return message
    if states.get("TLS_IPV4") == "OK" and states.get("WS_RAW_IPV4") == "FAIL":
        return "HTTPS/TLS funciona, pero el canal WebSocket Live queda bloqueado o no completa el upgrade."
    if "route" in categories and states.get("TLS_IPV4") == "OK":
        return "IPv6 no tiene ruta, pero IPv4 funciona; debe forzarse IPv4."
    if "websocket_timeout" in categories:
        return "La conexion WebSocket no responde dentro del plazo; no es un error de clave REST."
    return "Hay varios fallos; usar las lineas FAIL del informe para localizar la primera capa rota."


def save_report(summary: str) -> None:
    lines = [
        "JARVIS GEMINI LIVE DIAGNOSTIC",
        f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "Clave API: REDACTADA (nunca se guarda)",
        "",
    ]
    lines.extend(
        f"[{result.state}] {result.name}: {result.detail}"
        + (f" [categoria={result.category}]" if result.category else "")
        for result in RESULTS
    )
    lines.extend(("", f"DIAGNOSTICO: {summary}", ""))
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


async def async_probes(ipv4_tls: bool, ipv6_tls: bool) -> None:
    raw_v4 = False
    if ipv4_tls:
        raw_v4 = await probe_raw_websocket(socket.AF_INET, "WS_RAW_IPV4", generate=True)
        await probe_raw_websocket(
            socket.AF_INET,
            "WS_RAW_HEADER",
            generate=False,
            auth_mode="header",
        )
    else:
        add("WS_RAW_IPV4", "SKIP", "TLS IPv4 no funciona")
        add("WS_RAW_HEADER", "SKIP", "TLS IPv4 no funciona")
    if ipv6_tls:
        await probe_raw_websocket(socket.AF_INET6, "WS_RAW_IPV6", generate=False)
    else:
        add("WS_RAW_IPV6", "SKIP", "TLS IPv6 no funciona")

    try:
        from google.genai import types

        minimal = types.LiveConnectConfig(response_modalities=["AUDIO"])
        await probe_sdk("SDK_MIN_AUTO", minimal, force_ipv4=False)
        if ipv4_tls:
            await probe_sdk("SDK_MIN_IPV4", minimal, force_ipv4=True)
        else:
            add("SDK_MIN_IPV4", "SKIP", "TLS IPv4 no funciona")
        if raw_v4:
            try:
                full = build_jarvis_config()
                await probe_sdk("SDK_JARVIS", full, force_ipv4=True)
            except Exception as exc:
                add("SDK_JARVIS", "FAIL", f"no se pudo construir config: {type(exc).__name__}: {exc}", "config")
        else:
            add("SDK_JARVIS", "SKIP", "primero debe funcionar WS_RAW_IPV4")
    except Exception as exc:
        add("SDK", "FAIL", f"google-genai no disponible: {type(exc).__name__}: {exc}", "sdk")


def main() -> int:
    print("JARVIS: diagnostico seguro de Gemini Live (normalmente 1-2 min.)")
    print("La clave no se mostrara ni se guardara.\n")
    configured = load_configuration()
    probe_environment()
    probe_dns()
    ipv4_tls = probe_tls(socket.AF_INET, "TLS_IPV4")
    ipv6_tls = probe_tls(socket.AF_INET6, "TLS_IPV6")

    if not configured:
        summary = diagnosis()
        save_report(summary)
        print(f"\nDIAGNOSTICO: {summary}\nInforme: {REPORT_FILE}")
        return 2

    rest_family = socket.AF_INET if ipv4_tls else socket.AF_INET6
    if ipv4_tls or ipv6_tls:
        probe_rest(rest_family)
    else:
        add("AUTH_HTTPS", "SKIP", "no hay TLS funcional")
        add("MODELO", "SKIP", "no hay TLS funcional")
    asyncio.run(async_probes(ipv4_tls, ipv6_tls))

    summary = diagnosis()
    save_report(summary)
    print(f"\nDIAGNOSTICO: {summary}")
    print(f"Informe seguro para enviar: {REPORT_FILE}")
    return 0 if not any(item.state == "FAIL" for item in RESULTS) else 2


if __name__ == "__main__":
    raise SystemExit(main())
