"""Agent orchestration loop.

Coordinates the interaction between user, LLM, and tools.
Depends on specialized modules for protocol parsing, message assembly,
session persistence, and tool execution.
"""

import json

import messages as msg
import prompts
import protocol
import session as session_module
import tools


def _inject_project_once(payload):
    """Inject PROJECT.md as a system message once per session."""
    workdir = payload.get("workdir", "")
    ctx = msg.load_project_context(workdir)
    if not ctx:
        return
    # Don't inject if already present in this session
    marker = "## Project context (PROJECT.md)"
    for m in payload["messages"]:
        if isinstance(m.get("content"), str) and marker in m["content"]:
            return
    session_module.append_message(payload, ctx["role"], ctx["content"])


def _finish(session_payload, content):
    """Persist assistant response and return it."""
    session_module.append_message(session_payload, "assistant", content)
    session_module.save_session(session_payload)
    return content


def _call_model_with_retry(client, messages, settings):
    """Call the LLM, retrying on malformed JSON responses.

    Returns (raw_output, parsed_dict) on success.
    On total failure, returns (raw_output, {"type":"final","content":raw_output}).
    """
    attempts = settings["model_retry_limit"] + 1
    retry_messages = list(messages)

    for attempt in range(attempts):
        raw_output = client.chat(retry_messages)
        parsed, error = protocol.parse_model_output(raw_output)

        if parsed is not None:
            return raw_output, parsed

        protocol.log_malformed_output(
            settings["logs_dir"],
            "model_retry_attempt_%s" % (attempt + 1),
            raw_output,
            error,
        )

        if attempt >= attempts - 1:
            break

        retry_messages = list(retry_messages)
        retry_messages.append({"role": "assistant", "content": protocol.strip_code_fence(raw_output)})
        retry_messages.append({"role": "system", "content": prompts.build_retry_prompt(attempt + 1, error)})

    return raw_output, {"type": "final", "content": raw_output}


def handle_user_message(client, settings, session_payload, user_input):
    """Process a user message through the tool-call loop and return the final answer.

    Dynamic step budget: starts at min_steps, extends by extension_step when the
    model is making productive progress, up to max_steps hard cap.
    """
    session_module.append_message(session_payload, "user", user_input)

    _inject_project_once(session_payload)

    seen_calls = set()

    min_steps = settings.get("min_steps", 10)
    max_steps = settings.get("max_steps", 25)
    extension = settings.get("extension_step", 5)

    budget = min_steps
    productive = 0

    for step in range(max_steps):
        if step >= budget:
            if productive >= 2 and budget < max_steps:
                budget = min(budget + extension, max_steps)
                productive = 0
            else:
                return _finish(
                    session_payload,
                    "Stopped after %d steps (budget %d, unproductive)." % (step, budget),
                )

        messages = msg.build_messages_for_turn(session_payload, settings)
        raw_output, parsed = _call_model_with_retry(client, messages, settings)

        if parsed.get("type") == "final":
            return _finish(session_payload, protocol.extract_final_content(parsed))

        if parsed.get("type") == "tool_call":
            tool_name = parsed.get("tool", "")
            arguments = parsed.get("arguments", {})

            call_key = json.dumps({"tool": tool_name, "arguments": arguments}, ensure_ascii=False, sort_keys=True)
            if call_key in seen_calls:
                return _finish(session_payload, "Stopped because the model repeated the same tool call without making progress.")

            seen_calls.add(call_key)
            args_str = ", ".join("%s=%s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in arguments.items())
            print("  \033[90m#%d %s(%s)\033[0m" % (step + 1, tool_name, args_str), flush=True)
            result = tools.run_tool(tool_name, arguments, settings)

            if result.get("ok"):
                productive += 1

            session_module.append_tool_call(session_payload, tool_name, arguments, result)
            session_module.append_message(session_payload, "assistant", raw_output)
            session_module.append_message(
                session_payload, "system",
                protocol.format_tool_result_message(tool_name, arguments, result),
            )
            session_module.save_session(session_payload)
            continue

        return _finish(session_payload, protocol.extract_final_content(parsed))

    return _finish(session_payload, "Stopped after too many tool steps.")
