"""CLI entry point.

Minimal: builds the argument parser, loads settings, dispatches to cli.
"""

import argparse
import json
import sys

import cli
import config


def build_parser():
    parser = argparse.ArgumentParser(description="Local CLI AI assistant")
    parser.set_defaults(session_id=None)
    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser("config", help="Store local config in project data directory")
    config_parser.add_argument("--provider", dest="provider", help="LLM provider: openai_compatible or mattermost")
    config_parser.add_argument("--api-key", dest="api_key", help="LLM API key (openai_compatible)")
    config_parser.add_argument("--base-url", dest="base_url", help="LLM base URL")
    config_parser.add_argument("--model", dest="model", help="LLM model (openai_compatible), or model_key (mattermost)")
    config_parser.add_argument("--debug", dest="debug", choices=["on", "off"], help="Enable or disable LLM request debug logging")
    config_parser.add_argument("--show", action="store_true", help="Print current local config")
    config_parser.add_argument("--init-template", action="store_true", help="Write a starter config template if the local config file does not exist")
    return parser


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


def _flag_to_config(updated, provider_name, field, value):
    if not value:
        return
    section = updated.setdefault("providers", {}).setdefault(updated.get(provider_name, "default"), {})
    section[field] = value


def main():
    try:
        settings = config.load_settings()
        parser = build_parser()
        args = parser.parse_args()

        if args.command is None:
            return cli.run_chat(args, settings)
        if args.command == "config":
            return run_config(args)

        parser.print_help()
        return 1
    except RuntimeError as exc:
        print("error:", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
