"""Tool execution and state transitions for the orchestrator."""

import json

import phase as ph
import prompts
import protocol
import session as session_module
import state as st
import tools


def advance_phase(state_obj, payload):
    """Check and apply the next phase. Returns True if phase changed."""
    next_phase = ph.get_next_phase(state_obj)
    if next_phase == state_obj.phase:
        return False
    old = state_obj.phase
    state_obj.phase = next_phase
    new_tools = ph.PHASE_TOOLS.get(next_phase, [])
    session_module.append_message(payload, "system",
        "Phase: %s → %s. New tools: %s" % (
            old.value, next_phase.value, ", ".join(new_tools) or "none"))
    return True


def apply_report_result(result, state_obj, payload):
    """Apply a report tool result to the current agent state."""
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
        return

    if report_type == "plan":
        state_obj.steps = [
            st.AgentStep(
                id="step-%d" % (index + 1),
                intent=step.get("intent", ""),
                target_files=step.get("target_files", []),
            )
            for index, step in enumerate(result["steps"])
        ]
        state_obj.current_step_index = 0
        session_module.append_message(payload, "system",
            "Plan accepted: %d steps. Starting step 1: %s" % (
                len(state_obj.steps), state_obj.steps[0].intent if state_obj.steps else "",
            ))
        return

    if report_type == "blocked":
        step = state_obj.current_step()
        if step:
            step.status = "failed"
            step.error = "[%s] %s" % (result.get("reason_type", "other"), result.get("detail", ""))
            step.retry_count += 1
        action = result.get("suggested_action", "retry")
        session_module.append_message(payload, "system",
            "Step blocked: %s. Suggested action: %s." % (step.error if step else "unknown", action))
        return

    if report_type == "done":
        state_obj.phase = st.Phase.DONE
        session_module.append_message(payload, "system",
            "Task marked done: %s" % result.get("summary", ""))


def execute_tool_call(tool_name, arguments, provider, state_obj, payload, settings, state_manager, seen_calls):
    """Execute one tool call, update session/state, and return (ok, marker)."""
    allowed = ph.PHASE_TOOLS.get(state_obj.phase, [])

    if tool_name not in allowed:
        session_module.append_message(payload, "system",
            "Tool '%s' is not available in phase %s. Available: %s" % (
                tool_name, state_obj.phase.value, ", ".join(allowed),
            ))
        correction = prompts.build_correction_prompt(provider, state_obj.phase, "invalid_tool", allowed)
        if correction:
            session_module.append_message(payload, "system", correction)
        session_module.save_session(payload)
        state_obj.iteration_count += 1
        state_manager.save(state_obj)
        return False, "invalid_tool"

    call_key = json.dumps({"tool": tool_name, "arguments": arguments}, ensure_ascii=False, sort_keys=True)
    if call_key in seen_calls:
        session_module.append_message(payload, "system", "Repeated call blocked.")
        correction = prompts.build_correction_prompt(provider, state_obj.phase, "repeated_call", allowed)
        if correction:
            session_module.append_message(payload, "system", correction)
        state_manager.save(state_obj)
        return False, "repeated_call"

    seen_calls.add(call_key)
    print("  \033[90m[%s] #%d %s(%s)\033[0m" % (
        state_obj.phase.value, state_obj.iteration_count + 1, tool_name,
        ", ".join("%s=%s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in arguments.items()),
    ), flush=True)

    result = tools.run_tool(tool_name, arguments, settings)

    if (state_obj.phase == st.Phase.PATCHING
            and tool_name == "write_file"
            and result.get("ok")):
        _mark_step_written(state_obj, arguments)
        advance_phase(state_obj, payload)

    if result.get("_report"):
        apply_report_result(result, state_obj, payload)
        advance_phase(state_obj, payload)

    session_module.append_tool_call(payload, tool_name, arguments, result)
    session_module.append_message(
        payload, "system",
        protocol.format_tool_result_message(tool_name, arguments, result),
    )
    session_module.save_session(payload)
    state_obj.iteration_count += 1
    state_manager.save(state_obj)
    return True, result


def _mark_step_written(state_obj, arguments):
    step = state_obj.current_step()
    if step:
        step.status = "done"
        step.result = "written: %s" % arguments.get("path", "?")
        state_obj.facts.files_modified.append(arguments.get("path", ""))
    if state_obj.current_step_index < len(state_obj.steps) - 1:
        state_obj.current_step_index += 1
