"""Message history management and assembly for the agent loop.

Responsible for building the message list sent to the LLM,
including history trimming and role normalization.
"""

import prompts


def trim_history(messages, max_history_messages):
    """Keep only the last N messages for context window limits."""
    if len(messages) <= max_history_messages:
        return list(messages)
    return list(messages[-max_history_messages:])


def normalize_history(messages):
    """Map non-standard roles to user role for LLMs without native tool support."""
    normalized = []
    for item in messages:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role not in ("system", "user", "assistant"):
            normalized.append({"role": "user", "content": str(content)})
        else:
            normalized.append({"role": role, "content": content})
    return normalized


def build_messages_for_turn(session_payload, settings):
    """Assemble the full message sequence for one LLM turn."""
    workdir = session_payload.get("workdir") or settings.get("workdir", "")
    messages = [
        {"role": "system", "content": prompts.SYSTEM_PROMPT},
        {"role": "system", "content": prompts.build_tool_prompt()},
        {"role": "system", "content": "Current working directory: %s" % workdir},
    ]
    messages.extend(
        normalize_history(
            trim_history(session_payload["messages"], settings["max_history_messages"])
        )
    )
    return messages
