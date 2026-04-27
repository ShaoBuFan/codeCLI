"""CLI command implementations.

Each run_* function corresponds to a subcommand from main.py's argument parser.
Contains slash-command handling, display formatting, and provider management.
"""

import datetime
import json
import os

import agent
import config
import files
import init
import llm_client
import orchestrator
import session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_json_like(result):
    """Pretty-print a tool result dict."""
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
    print("provider:   %s (%s)" % (settings["active_provider"], settings["llm_provider"]))
    print("model:      %s" % settings.get("llm_model") or settings.get("llm_model_key", ""))
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
        return
    local_config["active_provider"] = name
    config.save_local_config(local_config)
    print("active_provider:", name)


def _is_tool_call_json(content):
    """Check if assistant content is a tool call JSON, not a final answer."""
    if not isinstance(content, str) or not content.startswith("{"):
        return False
    try:
        data = json.loads(content)
        return isinstance(data, dict) and data.get("type") == "tool_call"
    except (json.JSONDecodeError, ValueError):
        return False


def _display_session_history(messages):
    """Print the last meaningful user-assistant exchange from loaded history."""
    last_user = None
    last_assistant = None

    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "assistant" and last_assistant is None and not _is_tool_call_json(content):
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




# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

# Each handler receives (settings, payload, client, args_list).
# Returns (handled, should_exit, rebuild_client).

def _cmd_help(_, _1, _2, _3):
    _show_help()
    return True, False, False

def _cmd_cwd(_, p, _1, _2):
    print(p["workdir"])
    return True, False, False

def _cmd_session(_, p, _1, _2):
    print(p["session_id"])
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

def _cmd_provider(s, _1, _2, args):
    if not args:
        _show_provider_status()
        return True, False, False
    _switch_provider(args[0])
    workdir = s.get("workdir")
    new_settings = config.load_settings()
    s.clear()
    s.update(new_settings)
    s["workdir"] = workdir
    return True, False, True

def _cmd_load(s, p, _1, args):
    if not args:
        print("usage: /load <session_id>")
        return True, False, False
    try:
        new_payload = session.load_session(args[0])
    except RuntimeError as exc:
        print("error:", exc)
        return True, False, False
    p.clear()
    p.update(new_payload)
    s["workdir"] = p["workdir"]
    print("loaded  %s" % p["session_id"])
    print("workdir %s" % p["workdir"])
    _display_session_history(p["messages"])
    return True, False, False

def _cmd_files(s, p, _1, args):
    path = args[0] if args else "."
    result = files.list_files(root=p["workdir"], relative_path=path, recursive=True)
    _print_json_like(result)
    return True, False, False

def _cmd_read(s, p, _1, args):
    if not args:
        print("usage: /read <path>")
        return True, False, False
    result = files.read_file(root=p["workdir"], relative_path=args[0],
                             max_bytes=s["max_file_bytes"])
    _print_json_like(result)
    return True, False, False

def _cmd_search(s, p, _1, args):
    if not args:
        print("usage: /search <keyword> [path]")
        return True, False, False
    keyword = args[0]
    path = args[1] if len(args) >= 2 else "."
    result = files.search_text(root=p["workdir"], keyword=keyword, relative_path=path)
    _print_json_like(result)
    return True, False, False

def _cmd_config(s, _1, _2, _3):
    _show_config(s)
    return True, False, False

def _cmd_init(s, p, c, _):
    init.run(s, p, c)
    return True, False, False

def _cmd_clear(_, p, _1, _2):
    new_payload = session.create_session(p["workdir"])
    p.clear()
    p.update(new_payload)
    print("new session:", p["session_id"])
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


def _run_slash_command(user_input, settings, payload, client):
    """Handle a slash-prefixed command. Returns (handled, should_exit, rebuild_client)."""
    parts = user_input.strip().split()
    command = parts[0].lower()
    handler = _COMMANDS.get(command)

    if handler:
        return handler(settings, payload, client, parts[1:])

    print("unknown command:", command)
    print("use /help")
    return True, False, False


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _get_session_payload(session_id):
    """Load an existing session or create a new one."""
    if session_id:
        return session.load_session(session_id)
    return session.create_session(os.getcwd())


def _build_client(settings):
    return llm_client.build_client(settings)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _handle_message(client, settings, payload, user_input, mode):
    if mode == "stateful":
        return orchestrator.run(client, settings, payload, user_input)
    return agent.handle_user_message(client, settings, payload, user_input)


def run_ask(args, settings):
    """Execute the 'ask' subcommand: single question → answer."""
    payload = _get_session_payload(args.session_id)
    settings["workdir"] = payload["workdir"]
    client = _build_client(settings)
    mode = getattr(args, "mode", "legacy")
    answer = _handle_message(client, settings, payload, args.message, mode)
    print()
    print(answer)
    return 0


def run_chat(args, settings):
    """Execute the 'chat' subcommand: interactive REPL."""
    payload = _get_session_payload(args.session_id)
    settings["workdir"] = payload["workdir"]
    client = _build_client(settings)
    mode = getattr(args, "mode", "legacy")

    model = settings.get("llm_model") or settings.get("llm_model_key", "?")
    print("\033[1;36mcodeCLI\033[0m  \033[90m%s / %s\033[0m" % (settings["active_provider"], model))
    if mode == "stateful":
        print("\033[90mmode     stateful (phase-driven)\033[0m")
    print("\033[90mworkdir  %s\033[0m" % settings["workdir"])
    print("\033[90msession  %s\033[0m" % payload["session_id"])
    if payload["messages"]:
        _display_session_history(payload["messages"])
    print("\033[90mtype /help\033[0m")

    while True:
        try:
            user_input = input("\033[1mYou ›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue
        if user_input.startswith("/"):
            handled, should_exit, rebuild = _run_slash_command(user_input, settings, payload, client)
            if should_exit:
                return 0
            if rebuild:
                client = _build_client(settings)
                model = settings.get("llm_model") or settings.get("llm_model_key", "?")
            if handled:
                continue

        answer = _handle_message(client, settings, payload, user_input, mode)
        print()
        print(answer)
        print("\033[90m──\033[0m")


def run_sessions():
    """Execute the 'sessions' subcommand: list all saved sessions."""
    items = session.list_sessions()
    if not items:
        print("no sessions")
        return 0
    for item in items:
        ts = datetime.datetime.fromtimestamp(item["updated_at"]).strftime("%m-%d %H:%M")
        print("  %s  %s  %s" % (ts, item["session_id"], item["workdir"]))
    return 0


def run_config(args):
    """Execute the 'config' subcommand: view or update local config."""
    if args.show:
        print(json.dumps(config.load_local_config(), ensure_ascii=False, indent=2))
        return 0

    if args.init_template:
        path = config.local_config_path()
        if path.exists():
            print("exists:", path)
            return 0
        config.save_local_config(config.config_template())
        print("created template:", path)
        return 0

    current = config.load_local_config()
    updated = dict(current)

    if args.provider:
        updated["active_provider"] = args.provider
        providers = updated.setdefault("providers", {})
        section = providers.get(args.provider)
        if not isinstance(section, dict):
            section = {}
        if "llm_provider" not in section:
            # Derive from template if the name matches a known provider
            template = config.config_template().get("providers", {}).get(args.provider)
            if template and "llm_provider" in template:
                section["llm_provider"] = template["llm_provider"]
        providers[args.provider] = section

    # Map CLI flags → provider config fields
    _flag_to_config(updated, "active_provider", "llm_api_key", args.api_key)
    _flag_to_config(updated, "active_provider", "llm_base_url", args.base_url)
    _flag_to_config(updated, "active_provider", "llm_model", args.model)

    if args.debug:
        updated["llm_debug"] = args.debug == "on"

    config.save_local_config(updated)
    print("saved:", config.local_config_path())
    return 0


def _flag_to_config(updated, provider_name, field, value):
    """Set *value* on the active provider's config block if it is truthy."""
    if not value:
        return
    section = updated.setdefault("providers", {}).setdefault(updated.get(provider_name, "default"), {})
    section[field] = value
