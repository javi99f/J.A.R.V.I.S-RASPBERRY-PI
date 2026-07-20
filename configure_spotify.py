from __future__ import annotations

import getpass
import os
from pathlib import Path

from omar_ai_core.settings import get_secret, write_runtime_settings
from omar_ai_core.tools.spotify_control import SPOTIFY_SCOPES, SpotifySettings


def _ask(label: str, current: str = "", *, secret: bool = False) -> str:
    suffix = " [already configured]" if current else ""
    prompt = f"{label}{suffix}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    return value.strip() or current


def main() -> int:
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        print("Spotipy is missing. Run: ./.venv/bin/python -m pip install -r requirements.txt")
        return 1

    print("\nJARVIS Spotify setup")
    print("The client secret is entered invisibly and remains only in ~/Jarvis/.env.\n")
    client_id = _ask("Spotify Client ID", get_secret("SPOTIFY_CLIENT_ID"))
    client_secret = _ask(
        "Spotify Client Secret", get_secret("SPOTIFY_CLIENT_SECRET"), secret=True
    )
    redirect_uri = _ask(
        "Redirect URI",
        get_secret("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
    )
    device_name = _ask(
        "Spotify Connect device name",
        get_secret("SPOTIFY_DEVICE_NAME", "JARVIS Raspberry Pi"),
    )
    if not client_id or not client_secret:
        print("Client ID and Client Secret are required.")
        return 1

    write_runtime_settings(
        {
            "SPOTIFY_CLIENT_ID": client_id,
            "SPOTIFY_CLIENT_SECRET": client_secret,
            "SPOTIFY_REDIRECT_URI": redirect_uri,
            "SPOTIFY_DEVICE_NAME": device_name,
        }
    )
    settings = SpotifySettings.load()
    settings.cache_path.parent.mkdir(parents=True, exist_ok=True)
    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPES,
        cache_path=str(settings.cache_path),
        open_browser=False,
    )
    token = auth.get_cached_token()
    if not token:
        print("\n1. Open this URL on a computer or phone and approve access:\n")
        print(auth.get_authorize_url())
        print(
            "\n2. Spotify will redirect to a page that may fail to open. "
            "Copy the COMPLETE URL from the address bar and paste it below."
        )
        redirected = input("Redirected URL: ").strip()
        code = auth.parse_response_code(redirected)
        if not code:
            print("No authorization code was found in that URL.")
            return 1
        auth.get_access_token(code, check_cache=False)

    if settings.cache_path.exists():
        try:
            os.chmod(settings.cache_path, 0o600)
        except OSError:
            pass

    client = spotipy.Spotify(auth_manager=auth, requests_timeout=12, retries=2)
    profile = client.current_user() or {}
    print(f"\nSpotify connected as: {profile.get('display_name') or profile.get('id')}")
    devices = (client.devices() or {}).get("devices", [])
    if devices:
        print("Available Spotify Connect devices:")
        for device in devices:
            print(f"- {device.get('name')} ({device.get('type')})")
    else:
        print("Authorization works. Start raspotify so the Raspberry appears as a device.")
    print("Spotify setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
