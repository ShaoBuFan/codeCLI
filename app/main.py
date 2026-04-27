"""CLI entry point.

Minimal: builds the argument parser, loads settings, dispatches to cli.
"""

import argparse
import sys

import cli
import config


def build_parser():
    parser = argparse.ArgumentParser(description="Local CLI AI assistant")
    subparsers = parser.add_subparsers(dest="command")

    ask_parser = subparsers.add_parser("ask", help="Ask one question")
    ask_parser.add_argument("message", help="Question for the assistant")
    ask_parser.add_argument("--session", dest="session_id", help="Reuse a session id")
    ask_parser.add_argument("--mode", dest="mode", choices=["legacy", "stateful"], default="legacy",
                            help="Agent mode: legacy (default) or stateful")

    chat_parser = subparsers.add_parser("chat", help="Start interactive chat")
    chat_parser.add_argument("--session", dest="session_id", help="Reuse a session id")
    chat_parser.add_argument("--mode", dest="mode", choices=["legacy", "stateful"], default="legacy",
                            help="Agent mode: legacy (default) or stateful")

    subparsers.add_parser("sessions", help="List local sessions")

    config_parser = subparsers.add_parser("config", help="Store local config in project data directory")
    config_parser.add_argument("--provider", dest="provider", help="LLM provider: openai_compatible or mattermost")
    config_parser.add_argument("--api-key", dest="api_key", help="LLM API key (openai_compatible)")
    config_parser.add_argument("--base-url", dest="base_url", help="LLM base URL")
    config_parser.add_argument("--model", dest="model", help="LLM model (openai_compatible), or model_key (mattermost)")
    config_parser.add_argument("--debug", dest="debug", choices=["on", "off"], help="Enable or disable LLM request debug logging")
    config_parser.add_argument("--show", action="store_true", help="Print current local config")
    config_parser.add_argument("--init-template", action="store_true", help="Write a starter config template if the local config file does not exist")
    return parser


def main():
    try:
        settings = config.load_settings()
        parser = build_parser()
        args = parser.parse_args()

        if args.command == "ask":
            return cli.run_ask(args, settings)
        if args.command == "chat":
            return cli.run_chat(args, settings)
        if args.command == "sessions":
            return cli.run_sessions()
        if args.command == "config":
            return cli.run_config(args)

        parser.print_help()
        return 1
    except RuntimeError as exc:
        print("error:", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
