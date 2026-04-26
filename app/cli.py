"""CLI command implementations.

Each run_* function corresponds to a subcommand from main.py's argument parser.
Contains slash-command handling, display formatting, and provider management.
"""

import json
import os

import agent
import config
import files
import llm_client
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
    print("Available commands:")
    print("/help")
    print("/cwd")
    print("/provider")
    print("/provider <name>")
    print("/session")
    print("/sessions")
    print("/load <session_id>")
    print("/files [path]")
    print("/read <path>")
    print("/search <keyword> [path]")
    print("/config")
    print("/clear")
    print("/verbose")
    print("/exit")


def _show_config(settings):
    print("active_provider:", settings["active_provider"])
    print("project_root:", settings["project_root"])
    print("workdir:", settings["workdir"])
    print("llm_provider:", settings["llm_provider"])
    print("llm_base_url:", settings["llm_base_url"])
    print("llm_model:", settings["llm_model"])
    print("llm_timeout:", settings["llm_timeout"])
    print("llm_response_path:", settings["llm_response_path"])
    print("llm_debug:", "on" if settings["llm_debug"] else "off")
    print("git_bash_path:", settings["git_bash_path"])
    print("api_key_configured:", "yes" if settings["llm_api_key"] else "no")


def _list_provider_names():
    providers = config.load_local_config().get("providers")
    return sorted(providers.keys()) if isinstance(providers, dict) else []


def _show_provider_status():
    settings = config.load_settings()
    names = _list_provider_names()
    print("active_provider:", settings["active_provider"])
    if not names:
        print("providers: none")
        return
    print("providers:")
    for name in names:
        marker = "* " if name == settings["active_provider"] else "  "
        print("%s%s" % (marker, name))


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


def _show_current_workdir(payload):
    print(payload["workdir"])


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
        print("--- last exchange ---")
        if last_user:
            print("  user:", last_user[:200])
        if last_assistant:
            print("  assistant:", last_assistant[:200])


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

def _run_slash_command(user_input, settings, payload):
    """Handle a slash-prefixed command. Returns (handled, should_exit, rebuild_client)."""
    parts = user_input.strip().split()
    command = parts[0].lower()

    if command == "/help":
        _show_help()
        return True, False, False

    if command == "/cwd":
        _show_current_workdir(payload)
        return True, False, False

    if command == "/provider":
        if len(parts) == 1:
            _show_provider_status()
        else:
            _switch_provider(parts[1])
            new_settings = config.load_settings()
            settings.clear()
            settings.update(new_settings)
        return True, False, True

    if command == "/session":
        print(payload["session_id"])
        return True, False, False

    if command == "/sessions":
        items = session.list_sessions()
        if not items:
            print("no sessions")
            return True, False, False
        for item in items:
            print("%s  %s  %s" % (item["session_id"], item["updated_at"], item["workdir"]))
        return True, False, False

    if command == "/load":
        if len(parts) < 2:
            print("usage: /load <session_id>")
            return True, False, False
        try:
            new_payload = session.load_session(parts[1])
        except RuntimeError as exc:
            print("error:", exc)
            return True, False, False
        payload.clear()
        payload.update(new_payload)
        settings["workdir"] = payload["workdir"]
        print("loaded session:", payload["session_id"])
        _display_session_history(payload["messages"])
        return True, False, False

    if command == "/files":
        path = " ".join(parts[1:]) if len(parts) >= 2 else "."
        result = files.list_files(root=payload["workdir"], relative_path=path, recursive=True)
        _print_json_like(result)
        return True, False, False

    if command == "/read":
        if len(parts) < 2:
            print("usage: /read <path>")
            return True, False, False
        path = " ".join(parts[1:])
        result = files.read_file(root=payload["workdir"], relative_path=path, max_bytes=settings["max_file_bytes"])
        _print_json_like(result)
        return True, False, False

    if command == "/search":
        if len(parts) < 2:
            print("usage: /search <keyword> [path]")
            return True, False, False
        keyword = parts[1]
        path = " ".join(parts[2:]) if len(parts) >= 3 else "."
        result = files.search_text(root=payload["workdir"], keyword=keyword, relative_path=path)
        _print_json_like(result)
        return True, False, False

    if command == "/config":
        _show_config(settings)
        return True, False, False

    if command == "/clear":
        new_payload = session.create_session(payload["workdir"])
        payload.clear()
        payload.update(new_payload)
        print("new session:", payload["session_id"])
        return True, False, False

    if command in ("/exit", "/quit"):
        return True, True, False

    if command == "/verbose":
        current = settings.get("show_tool_calls", False)
        settings["show_tool_calls"] = not current
        print("tool calls:", "on" if settings["show_tool_calls"] else "off")
        return True, False, False

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

def run_ask(args, settings):
    """Execute the 'ask' subcommand: single question → answer."""
    payload = _get_session_payload(args.session_id)
    settings["workdir"] = payload["workdir"]
    client = _build_client(settings)
    answer = agent.handle_user_message(client, settings, payload, args.message)
    print(answer)
    print("session:", payload["session_id"])
    return 0


def run_chat(args, settings):
    """Execute the 'chat' subcommand: interactive REPL."""
    payload = _get_session_payload(args.session_id)
    settings["workdir"] = payload["workdir"]
    client = _build_client(settings)

    print("session:", payload["session_id"])
    if payload["messages"]:
        _display_session_history(payload["messages"])
    print("type /exit to quit")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue
        if user_input.startswith("/"):
            handled, should_exit, rebuild = _run_slash_command(user_input, settings, payload)
            if should_exit:
                return 0
            if rebuild:
                client = _build_client(settings)
            if handled:
                continue

        answer = agent.handle_user_message(client, settings, payload, user_input)
        print(answer)


def run_sessions():
    """Execute the 'sessions' subcommand: list all saved sessions."""
    items = session.list_sessions()
    if not items:
        print("no sessions")
        return 0
    for item in items:
        print("%s  %s  %s" % (item["session_id"], item["updated_at"], item["workdir"]))
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
    _flag_to_config(updated, "active_provider", "llm_headers_json", args.headers_json)
    _flag_to_config(updated, "active_provider", "llm_body_template_json", args.body_template_json)
    _flag_to_config(updated, "active_provider", "llm_response_path", args.response_path)

    if args.debug:
        updated["llm_debug"] = args.debug == "on"
    if args.git_bash_path:
        updated["git_bash_path"] = args.git_bash_path

    config.save_local_config(updated)
    print("saved:", config.local_config_path())
    return 0


def _flag_to_config(updated, provider_name, field, value):
    """Set *value* on the active provider's config block if it is truthy."""
    if not value:
        return
    section = updated.setdefault("providers", {}).setdefault(updated.get(provider_name, "default"), {})
    section[field] = value
