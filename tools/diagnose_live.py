"""Layered Gemini Live diagnostic for Windows, macOS, and Raspberry Pi.

The script never prints the API key or a URL containing it.  Each stage
isolates a different failure boundary: DNS, TLS, REST authentication, raw
WebSocket authentication, and finally the GenAI SDK setup exchange.
"""

from __future__ import annotations

import asyncio
import os
import socket
import ssl
import sys
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import quote

import requests
from google import genai
from google.genai import types
from websockets.asyncio.client import connect as websocket_connect

from omar_ai_core.runtime import (
    LIVE_MODEL,
    JarvisLive,
    _configure_live_transport,
)
from omar_ai_core.settings import get_secret, require_secret


HOST = "generativelanguage.googleapis.com"
LIVE_ENDPOINT = (
    f"wss://{HOST}/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def _safe_error(exc: BaseException, key: str) -> str:
    # Defensive redaction matters for the official query-string auth test.
    message = str(exc).replace(key, "<redacted>")
    return f"{type(exc).__name__}:{message[:300]}"


def _timeout() -> float:
    try:
        return max(10.0, float(get_secret("LIVE_OPEN_TIMEOUT_SECONDS", "20")))
    except ValueError:
        return 20.0


def print_environment() -> None:
    print(f"PYTHON={sys.version.split()[0]}")
    print(f"GOOGLE_GENAI={_package_version('google-genai')}")
    print(f"WEBSOCKETS={_package_version('websockets')}")
    print(f"MODEL={LIVE_MODEL}")
    proxy_names = sorted(
        name for name in os.environ
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "ws_proxy", "wss_proxy", "no_proxy"}
    )
    print(f"PROXY_ENV={','.join(proxy_names) if proxy_names else 'none'}")


def check_dns() -> None:
    try:
        records = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)
        ipv4 = sorted({item[4][0] for item in records if item[0] == socket.AF_INET})
        ipv6 = sorted({item[4][0] for item in records if item[0] == socket.AF_INET6})
        print(f"DNS=OK:ipv4={len(ipv4)}:ipv6={len(ipv6)}")
    except Exception as exc:
        print(f"DNS=ERROR:{type(exc).__name__}:{str(exc)[:200]}")


async def check_tls() -> None:
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                HOST,
                443,
                family=socket.AF_INET,
                ssl=ssl.create_default_context(),
                server_hostname=HOST,
            ),
            timeout=_timeout(),
        )
        print("TLS_IPV4=OK")
    except Exception as exc:
        print(f"TLS_IPV4=ERROR:{type(exc).__name__}:{str(exc)[:200]}")
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()


async def check_rest(key: str) -> None:
    def request_models():
        return requests.get(
            f"https://{HOST}/v1beta/models",
            headers={"x-goog-api-key": key},
            timeout=_timeout(),
        )

    try:
        response = await asyncio.to_thread(request_models)
        print(f"REST={response.status_code}")
    except Exception as exc:
        print(f"REST=ERROR:{_safe_error(exc, key)}")


async def check_raw_websocket(key: str, *, query_auth: bool) -> None:
    name = "RAW_WS_QUERY" if query_auth else "RAW_WS_HEADER"
    uri = LIVE_ENDPOINT
    headers = None
    if query_auth:
        uri = f"{uri}?key={quote(key, safe='')}"
    else:
        headers = {"x-goog-api-key": key}

    try:
        async with websocket_connect(
            uri,
            additional_headers=headers,
            family=socket.AF_INET,
            proxy=None,
            open_timeout=_timeout(),
        ):
            print(f"{name}=OPEN")
    except Exception as exc:
        print(f"{name}=ERROR:{_safe_error(exc, key)}")


async def check_sdk(key: str, *, full: bool = False) -> None:
    name = "SDK_FULL" if full else "SDK_MINIMAL"
    config = (
        JarvisLive.__new__(JarvisLive)._build_config()
        if full
        else types.LiveConnectConfig(response_modalities=["AUDIO"])
    )
    _configure_live_transport()
    client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
    try:
        async with client.aio.live.connect(model=LIVE_MODEL, config=config):
            print(f"{name}=OK")
    except Exception as exc:
        print(f"{name}=ERROR:{_safe_error(exc, key)}")


async def main() -> None:
    key = require_secret("GEMINI_API_KEY")
    print_environment()
    check_dns()
    await check_tls()
    await check_rest(key)
    await check_raw_websocket(key, query_auth=False)
    await check_raw_websocket(key, query_auth=True)
    await check_sdk(key)
    if "--full" in sys.argv:
        await check_sdk(key, full=True)


if __name__ == "__main__":
    asyncio.run(main())
