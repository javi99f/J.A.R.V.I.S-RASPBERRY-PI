import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from omar_ai_core.settings import get_secret


def _project_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


CONFIG_PATH = _project_dir() / "config" / "home_assistant.json"


def _load_config() -> dict:
    config = {}
    if CONFIG_PATH.exists():
        try:
            config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")))
        except Exception:
            pass

    url = os.getenv("HOME_ASSISTANT_URL") or get_secret("HOME_ASSISTANT_URL") or config.get("url")
    token = os.getenv("HOME_ASSISTANT_TOKEN") or get_secret("HOME_ASSISTANT_TOKEN") or config.get("token")
    if url:
        config["url"] = str(url).rstrip("/")
    if token:
        config["token"] = str(token).strip()
    return config


def _request(method: str, path: str, payload: dict | None = None):
    cfg = _load_config()
    url = cfg.get("url", "").rstrip("/")
    token = cfg.get("token", "")
    if not url or not token:
        raise RuntimeError("Home Assistant URL/token is not configured.")

    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=12) as response:
        raw = response.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else {}


def _states() -> list[dict]:
    return _request("GET", "/api/states")


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _friendly(entity: dict) -> str:
    return str((entity.get("attributes") or {}).get("friendly_name") or entity.get("entity_id") or "")


def _controllable(entity: dict) -> bool:
    entity_id = str(entity.get("entity_id", ""))
    return entity_id.startswith(("light.", "switch."))


def _match_entities(target: str, domain: str = "any") -> list[dict]:
    target_norm = _norm(target)
    entities = [item for item in _states() if _controllable(item)]
    if domain in {"light", "switch"}:
        entities = [item for item in entities if str(item.get("entity_id", "")).startswith(domain + ".")]

    if not target_norm or target_norm in {"all", "all lights", "lights", "the lights"}:
        return [item for item in entities if str(item.get("entity_id", "")).startswith("light.")]

    matches = []
    for item in entities:
        haystack = _norm(f"{item.get('entity_id', '')} {_friendly(item)}")
        if target_norm in haystack or haystack in target_norm:
            matches.append(item)
    if matches:
        return matches

    target_words = set(target_norm.split())
    scored = []
    for item in entities:
        haystack_words = set(_norm(f"{item.get('entity_id', '')} {_friendly(item)}").split())
        score = len(target_words & haystack_words)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:3]]


def _call_service(entity_id: str, action: str, brightness_pct: int | None = None):
    domain = entity_id.split(".", 1)[0]
    service = {
        "turn_on": "turn_on",
        "turn_off": "turn_off",
        "toggle": "toggle",
    }.get(action, action)
    payload = {"entity_id": entity_id}
    if domain == "light" and service == "turn_on" and brightness_pct is not None:
        pct = max(1, min(100, int(brightness_pct)))
        payload["brightness"] = round(pct * 255 / 100)
    return _request("POST", f"/api/services/{domain}/{service}", payload)


def _entity_line(entity: dict) -> str:
    return f"{_friendly(entity)} ({entity.get('entity_id')}): {entity.get('state')}"


def home_control(parameters: dict | None = None, player=None, **_) -> str:
    params = parameters or {}
    action = str(params.get("action") or "status").lower().strip().replace("-", "_").replace(" ", "_")
    target = str(params.get("target") or params.get("entity") or params.get("description") or "").strip()
    domain = str(params.get("domain") or "any").lower().strip()
    brightness = params.get("brightness") or params.get("value")

    if action in {"list", "list_entities", "entities"}:
        entities = [item for item in _states() if _controllable(item)]
        if not entities:
            return "Home Assistant is connected, but no light or switch entities were found."
        return "Home Assistant devices:\n" + "\n".join(_entity_line(item) for item in entities[:40])

    entities = _match_entities(target, domain=domain)
    if not entities:
        return f"I could not find a Home Assistant light or switch matching '{target or 'that request'}'."

    if action in {"status", "state", "get_status"}:
        return "\n".join(_entity_line(item) for item in entities)

    all_targets = {_norm(v) for v in ("all", "all lights", "lights", "the lights", "todas", "todas las luces")}
    if len(entities) > 1 and _norm(target) not in all_targets:
        choices = ", ".join(_friendly(item) for item in entities[:5])
        return f"That name matches several devices: {choices}. Ask which one before changing anything."

    if action in {"turn_on", "on"}:
        service_action = "turn_on"
    elif action in {"turn_off", "off"}:
        service_action = "turn_off"
    elif action == "toggle":
        service_action = "toggle"
    elif action in {"brightness_set", "set_brightness"}:
        service_action = "turn_on"
    else:
        return "Unknown Home Assistant action. Try turn_on, turn_off, toggle, status, or list_entities."

    changed = []
    for entity in entities:
        entity_id = str(entity.get("entity_id"))
        _call_service(entity_id, service_action, int(brightness) if brightness is not None else None)
        changed.append(_friendly(entity))

    verb = {
        "turn_on": "turned on",
        "turn_off": "turned off",
        "toggle": "toggled",
    }.get(service_action, "updated")
    return f"Home Assistant {verb}: " + ", ".join(changed[:8])
