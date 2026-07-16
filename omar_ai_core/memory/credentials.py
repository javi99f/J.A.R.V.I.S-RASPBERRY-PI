from omar_ai_core.settings import get_secret, is_configured, write_env


def save_api_keys(gemini_api_key: str, openrouter_api_key: str = "", zernio_api_key: str = "") -> None:
    write_env(gemini_api_key, openrouter_api_key, zernio_api_key)


def load_api_keys() -> dict:
    return {
        "gemini_api_key": get_secret("GEMINI_API_KEY"),
        "openrouter_api_key": get_secret("OPENROUTER_API_KEY"),
        "zernio_api_key": get_secret("ZERNIO_API_KEY"),
    }


def get_gemini_key() -> str | None:
    return get_secret("GEMINI_API_KEY") or None
