"""Interactive REPL loop for the coding assistant."""

import os

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
            user_input = input("\033[1mYou \033[0m").strip()
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


def _get_session_payload(session_id):
    if session_id:
        return session.load_session(session_id)
    return session.create_session(os.getcwd())
