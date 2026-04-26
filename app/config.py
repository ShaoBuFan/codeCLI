import json
import os
from pathlib import Path


DEFAULT_BASE_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT = 120
DEFAULT_PROVIDER = "openai_compatible"


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
        "active_provider": "company",
        "git_bash_path": detect_git_bash(),
        "llm_debug": False,
        "providers": {
            "deepseek": {
                "llm_provider": "openai_compatible",
                "llm_api_key": "",
                "llm_base_url": "https://api.deepseek.com/chat/completions",
                "llm_model": "deepseek-v4-flash"
            },
            "company": {
                "llm_provider": "generic_json",
                "llm_api_key": "",
                "llm_base_url": "https://your-company-endpoint",
                "llm_model": "",
                "llm_headers": {
                    "Cookie": "...",
                    "Authorization": "Bearer ..."
                },
                "llm_body_template": {
                    "input": "{messages_text}"
                },
                "llm_response_path": "reply.text"
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
    raw = get_env("LLM_TIMEOUT", str(DEFAULT_TIMEOUT))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def detect_git_bash():
    candidates = [
        get_env("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\git-bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ]
    for item in candidates:
        if item and Path(item).exists():
            return item
    # Fallback to cmd.exe on Windows when Git Bash is not available
    cmd = get_env("COMSPEC", "cmd.exe")
    return cmd if Path(cmd).exists() else "cmd.exe"


def load_settings():
    ensure_directories()
    local_config = load_local_config()
    provider_config = _active_provider_config(local_config)
    return {
        "project_root": str(project_root()),
        "logs_dir": str(logs_dir()),
        "active_provider": _active_provider_name(local_config),
        "llm_provider": _pick_setting(local_config, provider_config, "llm_provider", "LLM_PROVIDER", DEFAULT_PROVIDER),
        "llm_api_key": _pick_setting(local_config, provider_config, "llm_api_key", "LLM_API_KEY", ""),
        "llm_base_url": _pick_setting(local_config, provider_config, "llm_base_url", "LLM_BASE_URL", DEFAULT_BASE_URL),
        "llm_model": _pick_setting(local_config, provider_config, "llm_model", "LLM_MODEL", DEFAULT_MODEL),
        "llm_timeout": _pick_setting(local_config, provider_config, "llm_timeout", "", get_timeout()),
        "llm_headers": _pick_setting(local_config, provider_config, "llm_headers", "", None),
        "llm_headers_json": _pick_setting(local_config, provider_config, "llm_headers_json", "LLM_HEADERS_JSON", ""),
        "llm_body_template": _pick_setting(local_config, provider_config, "llm_body_template", "", None),
        "llm_body_template_json": _pick_setting(local_config, provider_config, "llm_body_template_json", "LLM_BODY_TEMPLATE_JSON", ""),
        "llm_response_path": _pick_setting(local_config, provider_config, "llm_response_path", "LLM_RESPONSE_PATH", "content"),
        "git_bash_path": local_config.get("git_bash_path") or detect_git_bash(),
        "min_steps": 10,
        "max_steps": 25,
        "extension_step": 5,
        "max_history_messages": 16,
        "max_file_bytes": 200000,
        "max_shell_output_chars": 12000,
        "model_retry_limit": 2,
        "llm_debug": bool(local_config.get("llm_debug")) or get_env("LLM_DEBUG", "").lower() in ("1", "true", "yes", "on"),
        "show_tool_calls": False,
    }
