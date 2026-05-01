"""Top-level CLI entrypoints and interactive chat loop."""

import json
import os

import config
import llm_client
import orchestrator
import repl_commands
import session


def run_chat(args, settings):
    """Start the interactive REPL."""
    payload = _get_session_payload(getattr(args, "session_id", None))
    settings["workdir"] = payload["workdir"]
    client = llm_client.build_client(settings)

    model = settings.get("llm_model") or settings.get("llm_model_key", "?")
    print("\033[1;36mcodeCLI\033[0m  \033[90m%s / %s\033[0m" % (settings["active_provider"], model))
    print("\033[90mworkdir  %s\033[0m" % settings["workdir"])
    print("\033[90msession  %s\033[0m" % payload["session_id"])
    if payload["messages"]:
        repl_commands.display_session_history(payload["messages"])
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
            handled, should_exit, rebuild = repl_commands.run(user_input, settings, payload, client)
            if should_exit:
                return 0
            if rebuild:
                client = llm_client.build_client(settings)
            if handled:
                continue

        answer = orchestrator.run(client, settings, payload, user_input)
        print()
        print(answer)
        print("\033[90m──\033[0m")


def run_config(args):
    """View or update local config."""
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
            template = config.config_template().get("providers", {}).get(args.provider)
            if template and "llm_provider" in template:
                section["llm_provider"] = template["llm_provider"]
        providers[args.provider] = section

    _flag_to_config(updated, "active_provider", "llm_api_key", args.api_key)
    _flag_to_config(updated, "active_provider", "llm_base_url", args.base_url)
    _flag_to_config(updated, "active_provider", "llm_model", args.model)

    if args.debug:
        updated["llm_debug"] = args.debug == "on"

    config.save_local_config(updated)
    print("saved:", config.local_config_path())
    return 0


def _get_session_payload(session_id):
    if session_id:
        return session.load_session(session_id)
    return session.create_session(os.getcwd())


def _flag_to_config(updated, provider_name, field, value):
    if not value:
        return
    section = updated.setdefault("providers", {}).setdefault(updated.get(provider_name, "default"), {})
    section[field] = value
