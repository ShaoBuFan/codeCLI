"""Slash-command routing for the interactive REPL."""

import datetime
import json

import config
import files
import init
import session


def run(user_input, settings, payload, client):
    """Handle a slash-prefixed command. Returns (handled, should_exit, rebuild_client)."""
    parts = user_input.strip().split()
    command = parts[0].lower()
    handler = _COMMANDS.get(command)

    if handler:
        return handler(settings, payload, client, parts[1:])

    print("unknown command:", command)
    print("use /help")
    return True, False, False


def display_session_history(messages):
    """Print the last meaningful user-assistant exchange from loaded history."""
    last_user = None
    last_assistant = None

    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "assistant" and last_assistant is None and not _is_legacy_tool_call_json(content):
            last_assistant = content
        if role == "user" and last_user is None and not content.startswith("--- tool call result ---"):
            last_user = content
        if last_user is not None and last_assistant is not None:
            break

    if last_user is not None or last_assistant is not None:
        print("\033[90m┄┄┄ last exchange ┄┄┄\033[0m")
        if last_user:
            print(" \033[1mYou\033[0m  %s" % last_user[:200])
        if last_assistant:
            print(" \033[1mAI\033[0m   %s" % last_assistant[:200])
            print()


def _print_json_like(result):
    if result.get("ok") is False:
        print("error:", result.get("error", "unknown error"))
        return
    items = result.get("items")
    if isinstance(items, list):
        for item in items:
            print(item)
        if result.get("truncated"):
            print("[truncated]")
        return
    content = result.get("content")
    if isinstance(content, str):
        print(content)
        if result.get("truncated"):
            print("\n[truncated]")
        return
    results = result.get("results")
    if isinstance(results, list):
        if not results:
            print("no matches")
            return
        for item in results:
            print("%s:%s: %s" % (item.get("path", ""), item.get("line", ""), item.get("text", "")))
        if result.get("truncated"):
            print("[truncated]")
        return
    print(result)


def _show_help():
    print("Info              Session              Tools")
    print("  /help             /session             /files [path]")
    print("  /config           /sessions            /read <path>")
    print("  /cwd              /load <id>           /search <kw> [path]")
    print("  /provider [name]  /clear")
    print("  /init             /exit")


def _show_config(settings):
    key = "configured" if settings["llm_api_key"] else "not set"
    debug = "on" if settings["llm_debug"] else "off"
    model = settings.get("llm_model") or settings.get("llm_model_key", "")
    print("provider:   %s (%s)" % (settings["active_provider"], settings["llm_provider"]))
    print("model:      %s" % model)
    print("base_url:   %s" % settings["llm_base_url"])
    print("timeout:    %ss" % settings["llm_timeout"])
    print("debug:      %s" % debug)
    print("api_key:    %s" % key)
    print("workdir:    %s" % settings["workdir"])


def _list_provider_names():
    providers = config.load_local_config().get("providers")
    return sorted(providers.keys()) if isinstance(providers, dict) else []


def _show_provider_status():
    settings = config.load_settings()
    names = _list_provider_names()
    if not names:
        print("no providers configured")
        return
    for name in names:
        marker = "*" if name == settings["active_provider"] else " "
        print(" %s  %s" % (marker, name))


def _switch_provider(name):
    local_config = config.load_local_config()
    providers = local_config.get("providers")
    if not isinstance(providers, dict) or name not in providers:
        print("error: provider not found:", name)
        available = _list_provider_names()
        if available:
            print("available:", ", ".join(available))
        return False
    local_config["active_provider"] = name
    config.save_local_config(local_config)
    print("active_provider:", name)
    return True


def _is_legacy_tool_call_json(content):
    if not isinstance(content, str) or not content.startswith("{"):
        return False
    try:
        data = json.loads(content)
        return isinstance(data, dict) and data.get("type") == "tool_call"
    except (json.JSONDecodeError, ValueError):
        return False


def _cmd_help(_, _1, _2, _3):
    _show_help()
    return True, False, False


def _cmd_cwd(_, payload, _1, _2):
    print(payload["workdir"])
    return True, False, False


def _cmd_session(_, payload, _1, _2):
    print(payload["session_id"])
    return True, False, False


def _cmd_sessions(_, _1, _2, _3):
    items = session.list_sessions()
    if not items:
        print("no sessions")
        return True, False, False
    for item in items:
        ts = datetime.datetime.fromtimestamp(item["updated_at"]).strftime("%m-%d %H:%M")
        print("  %s  %s  %s" % (ts, item["session_id"], item["workdir"]))
    return True, False, False


def _cmd_provider(settings, _1, _2, args):
    if not args:
        _show_provider_status()
        return True, False, False
    if not _switch_provider(args[0]):
        return True, False, False
    workdir = settings.get("workdir")
    new_settings = config.load_settings()
    settings.clear()
    settings.update(new_settings)
    settings["workdir"] = workdir
    return True, False, True


def _cmd_load(settings, payload, _1, args):
    if not args:
        print("usage: /load <session_id>")
        return True, False, False
    try:
        new_payload = session.load_session(args[0])
    except RuntimeError as exc:
        print("error:", exc)
        return True, False, False
    payload.clear()
    payload.update(new_payload)
    settings["workdir"] = payload["workdir"]
    print("loaded  %s" % payload["session_id"])
    print("workdir %s" % payload["workdir"])
    display_session_history(payload["messages"])
    return True, False, False


def _cmd_files(settings, payload, _1, args):
    path = args[0] if args else "."
    result = files.list_files(root=payload["workdir"], relative_path=path, recursive=True)
    _print_json_like(result)
    return True, False, False


def _cmd_read(settings, payload, _1, args):
    if not args:
        print("usage: /read <path>")
        return True, False, False
    result = files.read_file(root=payload["workdir"], relative_path=args[0],
                             max_bytes=settings["max_file_bytes"])
    _print_json_like(result)
    return True, False, False


def _cmd_search(_, payload, _1, args):
    if not args:
        print("usage: /search <keyword> [path]")
        return True, False, False
    keyword = args[0]
    path = args[1] if len(args) >= 2 else "."
    result = files.search_text(root=payload["workdir"], keyword=keyword, relative_path=path)
    _print_json_like(result)
    return True, False, False


def _cmd_config(settings, _1, _2, _3):
    _show_config(settings)
    return True, False, False


def _cmd_init(settings, payload, client, _):
    init.run(settings, payload, client)
    return True, False, False


def _cmd_clear(_, payload, _1, _2):
    new_payload = session.create_session(payload["workdir"])
    payload.clear()
    payload.update(new_payload)
    print("new session:", payload["session_id"])
    return True, False, False


def _cmd_exit(_, _1, _2, _3):
    return True, True, False


_COMMANDS = {
    "/help":     _cmd_help,
    "/cwd":      _cmd_cwd,
    "/session":  _cmd_session,
    "/sessions": _cmd_sessions,
    "/provider": _cmd_provider,
    "/load":     _cmd_load,
    "/files":    _cmd_files,
    "/read":     _cmd_read,
    "/search":   _cmd_search,
    "/config":   _cmd_config,
    "/init":     _cmd_init,
    "/clear":    _cmd_clear,
    "/exit":     _cmd_exit,
    "/quit":     _cmd_exit,
}
