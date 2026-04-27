"""State-driven agent orchestrator.

Replaces the old model-remembers-everything loop with an external state
machine.  Each phase exposes only the tools it needs.  Report tools
update the state directly instead of relying on free-form model output.
"""

import json

import messages as msg
import phase as ph
import prompts
import protocol
import session as session_module
import state as st
import tools


def _finish(payload, content):
    session_module.append_message(payload, "assistant", content)
    session_module.save_session(payload)
    return content


def _call_model(client, messages, settings):
    """Single call with retry. Returns (raw, parsed) or (raw, fallback)."""
    attempts = settings["model_retry_limit"] + 1
    retry_messages = list(messages)

    for attempt in range(attempts):
        raw = client.chat(retry_messages)
        parsed, error = protocol.parse_model_output(raw)
        if parsed is not None:
            return raw, parsed

        protocol.log_malformed_output(
            settings["logs_dir"],
            "orchestrator_retry_%d" % (attempt + 1),
            raw, error,
        )
        if attempt >= attempts - 1:
            break
        retry_messages = list(retry_messages)
        retry_messages.append({"role": "assistant", "content": protocol.strip_code_fence(raw)})
        retry_messages.append({"role": "system", "content": prompts.build_retry_prompt(attempt + 1, error)})

    return raw, {"type": "final", "content": raw}


_TOOL_DESCRIPTIONS = [
    ("list_files",       'arguments: {"path":".","recursive":true,"pattern":"*.py"}'),
    ("read_file",        'arguments: {"path":"relative/path.txt"}'),
    ("search_text",      'arguments: {"keyword":"text","path":"."}'),
    ("write_file",       'arguments: {"path":"...","content":"full content"} (requires confirmation)'),
    ("report_findings",  'arguments: {"key_findings":["..."],"relevant_files":["..."],"constraints":["..."]}'),
    ("report_plan",      'arguments: {"steps":[{"intent":"...","target_files":["..."]}]}'),
    ("report_blocked",   'arguments: {"reason_type":"file_not_found|...","detail":"...","suggested_action":"retry|skip|replan|abort"}'),
    ("report_done",      'arguments: {"summary":"what was accomplished"}'),
]


def _build_tool_prompt(allowed):
    """Build a tool list including only the allowed tools, sequentially numbered."""
    lines = ["Available tools:"]
    n = 1
    for name, desc in _TOOL_DESCRIPTIONS:
        if name in allowed:
            lines.append("%d. %s  %s" % (n, name, desc))
            n += 1
    lines.append("")
    lines.append("Use only one tool per response.")
    return "\n".join(lines)


def _apply_report(result, state_obj, payload):
    """Apply a report tool result to the agent state.  Returns phase-change hint."""
    report_type = result.get("_report")
    if not report_type:
        return

    if report_type == "findings":
        state_obj.facts.key_findings = result["key_findings"]
        state_obj.facts.files_read = result["relevant_files"]
        state_obj.facts.constraints = result.get("constraints", [])
        session_module.append_message(payload, "system",
            "Findings recorded: %d findings, %d files, %d constraints." % (
                len(result["key_findings"]), len(result["relevant_files"]), len(result.get("constraints", [])),
            ))

    elif report_type == "plan":
        state_obj.steps = [
            st.AgentStep(
                id="step-%d" % (i + 1),
                intent=s.get("intent", ""),
                target_files=s.get("target_files", []),
            )
            for i, s in enumerate(result["steps"])
        ]
        state_obj.current_step_index = 0
        session_module.append_message(payload, "system",
            "Plan accepted: %d steps. Starting step 1: %s" % (
                len(state_obj.steps), state_obj.steps[0].intent if state_obj.steps else "",
            ))

    elif report_type == "blocked":
        step = state_obj.current_step()
        if step:
            step.status = "failed"
            step.error = "[%s] %s" % (result.get("reason_type", "other"), result.get("detail", ""))
            step.retry_count += 1
        action = result.get("suggested_action", "retry")
        session_module.append_message(payload, "system",
            "Step blocked: %s. Suggested action: %s." % (step.error if step else "unknown", action))

    elif report_type == "done":
        state_obj.phase = st.Phase.DONE
        session_module.append_message(payload, "system",
            "Task marked done: %s" % result.get("summary", ""))


def run(client, settings, payload, user_input):
    """State-driven orchestrator loop.  Returns the final answer string."""
    session_module.append_message(payload, "user", user_input)

    # Inject PROJECT.md once (same logic as agent._inject_project_once)
    ctx = msg.load_project_context(payload.get("workdir", ""))
    if ctx:
        marker = "## Project context (PROJECT.md)"
        if not any(marker in m.get("content", "") for m in payload["messages"]):
            session_module.append_message(payload, ctx["role"], ctx["content"])

    task_id = payload.get("orchestrator_task_id")
    sm = st.StateManager(task_id) if task_id else None
    state_obj = sm.load() if sm else None

    if state_obj is None:
        task_id = st.new_task_id()
        payload["orchestrator_task_id"] = task_id
        sm = st.StateManager(task_id)
        state_obj = st.AgentState(task_id=task_id, task_description=user_input, phase=st.Phase.EXPLORING)
        sm.save(state_obj)

    workdir = payload["workdir"]
    seen_calls = set()
    max_iter = state_obj.max_iterations

    for _ in range(max_iter):
        if state_obj.iteration_count >= max_iter:
            state_obj.phase = st.Phase.FAILED
            sm.save(state_obj)
            return _finish(payload, "Task failed: exceeded %d iterations." % max_iter)

        allowed = ph.PHASE_TOOLS.get(state_obj.phase, [])
        instruction = ph.PHASE_INSTRUCTIONS.get(state_obj.phase, "")

        # Build messages
        messages = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "system", "content": _build_tool_prompt(allowed)},
        ]
        if instruction:
            messages.append({"role": "system", "content": instruction})
        messages.append({"role": "system", "content": sm.to_prompt_context(state_obj)})
        messages.append({"role": "system", "content": "Current working directory: %s" % workdir})
        messages.extend(
            msg.normalize_history(
                msg.trim_history(payload["messages"], settings["max_history_messages"])
            )
        )

        raw_output, parsed = _call_model(client, messages, settings)

        if parsed.get("type") == "final":
            sm.save(state_obj)
            return _finish(payload, protocol.extract_final_content(parsed))

        if parsed.get("type") == "tool_call":
            tool_name = parsed.get("tool", "")
            arguments = parsed.get("arguments", {})

            if tool_name not in allowed:
                session_module.append_message(payload, "assistant", raw_output)
                session_module.append_message(payload, "system",
                    "Tool '%s' is not available in phase %s. Available: %s" % (
                        tool_name, state_obj.phase.value, ", ".join(allowed),
                    ))
                session_module.save_session(payload)
                state_obj.iteration_count += 1
                sm.save(state_obj)
                continue

            call_key = json.dumps({"tool": tool_name, "arguments": arguments}, ensure_ascii=False, sort_keys=True)
            if call_key in seen_calls:
                session_module.append_message(payload, "system", "Repeated call blocked.")
                sm.save(state_obj)
                return _finish(payload, "Stopped: repeated tool call.")

            seen_calls.add(call_key)
            print("  \033[90m[%s] #%d %s(%s)\033[0m" % (
                state_obj.phase.value, state_obj.iteration_count + 1, tool_name,
                ", ".join("%s=%s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in arguments.items()),
            ), flush=True)

            result = tools.run_tool(tool_name, arguments, settings)

            # Auto-advance step when a file write succeeds in PATCHING
            if (state_obj.phase == st.Phase.PATCHING
                    and tool_name == "write_file"
                    and result.get("ok")):
                step = state_obj.current_step()
                if step:
                    step.status = "done"
                    step.result = "written: %s" % arguments.get("path", "?")
                    state_obj.facts.files_modified.append(arguments.get("path", ""))
                if state_obj.current_step_index < len(state_obj.steps) - 1:
                    state_obj.current_step_index += 1

            if result.get("_report"):
                _apply_report(result, state_obj, payload)

                # Check phase transition
                next_phase = ph.get_next_phase(state_obj)
                if next_phase != state_obj.phase:
                    old = state_obj.phase
                    state_obj.phase = next_phase
                    session_module.append_message(payload, "system",
                        "Phase: %s → %s" % (old.value, next_phase.value))

            session_module.append_tool_call(payload, tool_name, arguments, result)
            session_module.append_message(payload, "assistant", raw_output)
            session_module.append_message(
                payload, "system",
                protocol.format_tool_result_message(tool_name, arguments, result),
            )
            session_module.save_session(payload)
            state_obj.iteration_count += 1
            sm.save(state_obj)
            continue

        # Unreachable: _call_model always returns final or tool_call
        session_module.append_message(payload, "assistant", raw_output)
        sm.save(state_obj)
        return protocol.extract_final_content(parsed)

    state_obj.phase = st.Phase.FAILED
    sm.save(state_obj)
    return _finish(payload, "Task failed: iteration limit.")
