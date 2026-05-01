"""Prompt-context assembly for the orchestrator."""

from pathlib import Path


def load_project_context(workdir):
    """Read PROJECT.md if it exists, returning it as a system message or None."""
    if not workdir:
        return None
    path = Path(workdir) / "PROJECT.md"
    try:
        content = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not content.strip():
        return None
    return {"role": "system", "content": "## Project context (PROJECT.md)\n\n%s" % content}


def ensure_project_context(payload):
    """Inject PROJECT.md into the session history once."""
    ctx = load_project_context(payload.get("workdir", ""))
    if not ctx:
        return
    marker = "## Project context (PROJECT.md)"
    if any(marker in m.get("content", "") for m in payload["messages"]):
        return
    payload["messages"].append(ctx)


def trim_history(messages, max_history_messages):
    """Keep only the last N messages for context window limits."""
    if len(messages) <= max_history_messages:
        return list(messages)
    return list(messages[-max_history_messages:])


def normalize_history(messages):
    """Map unknown roles to user role before sending to the model."""
    normalized = []
    for item in messages:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role not in ("system", "user", "assistant"):
            normalized.append({"role": "user", "content": str(content)})
        else:
            normalized.append({"role": role, "content": content})
    return normalized


def build_turn_messages(payload, settings, state_manager, state_obj, allowed, tool_prompt, instruction, phase_guidance):
    """Assemble the full message list for one orchestrator turn."""
    workdir = payload["workdir"]
    messages = [
        {"role": "system", "content": tool_prompt["base"]},
        {"role": "system", "content": tool_prompt["tools"]},
    ]
    if instruction:
        messages.append({"role": "system", "content": instruction})
    if phase_guidance:
        messages.append({"role": "system", "content": phase_guidance})
    messages.append({"role": "system", "content": state_manager.to_prompt_context(state_obj)})
    messages.append({"role": "system", "content": "Current working directory: %s" % workdir})
    messages.extend(
        normalize_history(
            trim_history(payload["messages"], settings["max_history_messages"])
        )
    )
    return messages
