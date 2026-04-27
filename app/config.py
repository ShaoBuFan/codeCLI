import json
import os
from pathlib import Path




def project_root():
    return Path(__file__).resolve().parent.parent


def data_dir():
    return project_root() / "data"


def sessions_dir():
    return data_dir() / "sessions"


def logs_dir():
    return data_dir() / "logs"


def local_config_path():
    return data_dir() / "local_config.json"


def ensure_directories():
    sessions_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)


def get_env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def load_local_config():
    path = local_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict):
        return data
    return {}


def save_local_config(data):
    ensure_directories()
    path = local_config_path()
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def config_template():
    return {
        "active_provider": "deepseek",
        "llm_debug": False,
        "providers": {
            "deepseek": {
                "llm_provider": "openai_compatible",
                "llm_api_key": "",
                "llm_base_url": "https://api.deepseek.com/chat/completions",
                "llm_model": "deepseek-v4-flash"
            },
            "mattermost": {
                "llm_provider": "mattermost",
                "llm_base_url": "https://mattermost.aslead.cloud/plugins/aslead-chatgpt",
                "llm_model_key": "sendMessageToChatGPT",
                "access_team": "",
                "mmauth_token": "",
                "mmuser_id": "",
                "csrf_token": ""
            }
        },
    }


def _provider_blocks(local_config):
    providers = local_config.get("providers")
    if isinstance(providers, dict):
        return providers
    return {}


def _active_provider_name(local_config):
    return local_config.get("active_provider") or local_config.get("llm_profile") or "default"


def _active_provider_config(local_config):
    providers = _provider_blocks(local_config)
    active_name = _active_provider_name(local_config)
    active = providers.get(active_name)
    if isinstance(active, dict):
        return active
    return {}


def _pick_setting(local_config, provider_config, key, env_name, default=None):
    value = provider_config.get(key)
    if value not in (None, ""):
        return value
    value = local_config.get(key)
    if value not in (None, ""):
        return value
    value = get_env(env_name, None)
    if value not in (None, ""):
        return value
    return default


def get_timeout():
    raw = get_env("LLM_TIMEOUT", "120")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 120


_RESOLVED = [
    # (key, env_name, default)  — default may be a callable for lazy evaluation
    ("llm_provider",   "LLM_PROVIDER",   ""),
    ("llm_api_key",    "LLM_API_KEY",    ""),
    ("llm_base_url",   "LLM_BASE_URL",   ""),
    ("llm_model",      "LLM_MODEL",      ""),
    ("llm_timeout",    "",               get_timeout),
    ("llm_headers",    "",               None),
    ("llm_model_key",  "",               ""),
    ("access_team",    "",               ""),
    ("mmauth_token",   "",               ""),
    ("mmuser_id",      "",               ""),
    ("csrf_token",     "",               ""),
]


def load_settings():
    ensure_directories()
    lc = load_local_config()
    pc = _active_provider_config(lc)

    settings = {
        "project_root":          str(project_root()),
        "logs_dir":              str(logs_dir()),
        "active_provider":       _active_provider_name(lc),
        "llm_debug":             bool(lc.get("llm_debug")) or get_env("LLM_DEBUG", "").lower() in ("1", "true", "yes", "on"),
        "min_steps":             10,
        "max_steps":             25,
        "extension_step":        5,
        "max_history_messages":  16,
        "max_file_bytes":        200000,
        "model_retry_limit":     2,
    }

    for key, env, default in _RESOLVED:
        d = default() if callable(default) else default
        settings[key] = _pick_setting(lc, pc, key, env, d)

    return settings
