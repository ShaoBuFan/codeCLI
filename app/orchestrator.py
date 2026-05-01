"""State-driven task loop for the coding agent."""

import messages as msg
import phase as ph
import prompts
import protocol
import session as session_module
import state as st
import tool_runtime


def run(client, settings, payload, user_input):
    """Run the state machine until a plain-text reply is ready."""
    session_module.append_message(payload, "user", user_input)
    msg.ensure_project_context(payload)

    state_manager, state_obj = _load_or_create_state(payload, user_input)
    seen_calls = set()

    while state_obj.iteration_count < state_obj.max_iterations:
        allowed = ph.PHASE_TOOLS.get(state_obj.phase, [])
        provider = settings.get("llm_provider", "openai_compatible")
        messages = _build_turn_messages(payload, settings, state_manager, state_obj, allowed, provider)
        raw_output, parsed, retries_exhausted = _call_model(client, messages, settings)

        if parsed.get("type") == "text":
            answer = _handle_text_reply(
                raw_output, parsed, retries_exhausted, provider, payload,
                state_obj, state_manager, allowed,
            )
            if answer is None:
                continue
            return answer

        if parsed.get("type") == "tool_calls":
            if _handle_tool_calls(parsed, provider, payload, settings, state_obj, state_manager, seen_calls):
                continue
            return _finish(payload, "Stopped: repeated tool call.")

        session_module.append_message(payload, "assistant", raw_output)
        state_manager.save(state_obj)
        return protocol.extract_text_content(parsed)

    state_obj.phase = st.Phase.FAILED
    state_manager.save(state_obj)
    return _finish(payload, "Task failed: exceeded %d iterations." % state_obj.max_iterations)


def _call_model(client, messages, settings):
    """Call the model with retry. Returns (raw, parsed, exhausted_flag)."""
    attempts = settings["model_retry_limit"] + 1
    retry_messages = list(messages)

    for attempt in range(attempts):
        raw = client.chat(retry_messages)
        parsed, error = protocol.parse_model_output(raw)
        if parsed is not None:
            return raw, parsed, False

        protocol.log_malformed_output(
            settings["logs_dir"],
            "orchestrator_retry_%d" % (attempt + 1),
            raw, error,
        )
        if attempt >= attempts - 1:
            break
        retry_messages = list(retry_messages)
        retry_messages.append({"role": "assistant", "content": protocol.strip_code_fence(raw)})
        retry_messages.append({
            "role": "system",
            "content": prompts.build_retry_prompt(
                attempt + 1, error, settings.get("llm_provider", "openai_compatible")
            ),
        })

    return raw, {"type": "text", "content": raw}, True


def _load_or_create_state(payload, user_input):
    task_id = payload.get("orchestrator_task_id")
    state_manager = st.StateManager(task_id) if task_id else None
    state_obj = state_manager.load() if state_manager else None

    if state_obj is None:
        task_id = st.new_task_id()
        payload["orchestrator_task_id"] = task_id
        state_manager = st.StateManager(task_id)
        state_obj = st.AgentState(task_id=task_id, task_description=user_input, phase=st.Phase.EXPLORING)
        state_manager.save(state_obj)

    return state_manager, state_obj


def _build_turn_messages(payload, settings, state_manager, state_obj, allowed, provider):
    instruction = ph.PHASE_INSTRUCTIONS.get(state_obj.phase, "")
    current_step = state_obj.current_step().intent if state_obj.current_step() else ""
    tool_prompt = {
        "base": prompts.build_base_prompt(settings),
        "tools": prompts.build_tool_prompt(provider, allowed, _recommended_action(state_obj, allowed)),
    }
    phase_guidance = prompts.build_phase_guidance(
        provider, state_obj.phase, allowed, current_step, state_obj.facts.files_modified
    )
    return msg.build_turn_messages(
        payload, settings, state_manager, state_obj, allowed, tool_prompt, instruction, phase_guidance
    )


def _handle_text_reply(raw_output, parsed, retries_exhausted, provider, payload, state_obj, state_manager, allowed):
    if retries_exhausted:
        state_manager.save(state_obj)
        return _finish(payload, protocol.extract_text_content(parsed))

    if state_obj.phase in (st.Phase.EXPLORING, st.Phase.PLANNING, st.Phase.PATCHING):
        session_module.append_message(payload, "assistant", raw_output)
        session_module.append_message(payload, "system", _blocked_text_reply_hint(state_obj.phase))
        correction = prompts.build_correction_prompt(provider, state_obj.phase, "premature_final", allowed)
        if correction:
            session_module.append_message(payload, "system", correction)
        session_module.save_session(payload)
        state_obj.iteration_count += 1
        state_manager.save(state_obj)
        return None

    state_manager.save(state_obj)
    return _finish(payload, protocol.extract_text_content(parsed))


def _handle_tool_calls(parsed, provider, payload, settings, state_obj, state_manager, seen_calls):
    session_module.append_message(payload, "assistant", parsed.get("content", "") or "")
    for call in parsed.get("tool_calls", []):
        ok, marker = tool_runtime.execute_tool_call(
            call.get("tool", ""),
            call.get("arguments", {}),
            provider,
            state_obj,
            payload,
            settings,
            state_manager,
            seen_calls,
        )
        if not ok:
            return marker != "repeated_call"
    return True


def _blocked_text_reply_hint(phase):
    hints = {
        st.Phase.EXPLORING: "You cannot give a plain text completion reply yet (phase: EXPLORING). Call report_findings to record what you learned.",
        st.Phase.PLANNING: "You cannot give a plain text completion reply yet (phase: PLANNING). Call report_plan with your step list to advance.",
        st.Phase.PATCHING: "You cannot give a plain text completion reply yet (phase: PATCHING). Call write_file to create the files — code in a plain text reply is not saved to disk.",
    }
    return hints.get(phase, "Use the available tools to complete the current step.")


def _finish(payload, content):
    session_module.append_message(payload, "assistant", content)
    session_module.save_session(payload)
    return content


def _recommended_action(state_obj, allowed):
    phase_name = state_obj.phase.value
    step = state_obj.current_step()
    step_files = step.target_files if step else []

    if phase_name == "EXPLORING":
        if state_obj.facts.key_findings:
            return "call report_findings now"
        if "list_files" in allowed and not state_obj.facts.files_read:
            return 'call list_files with {"path": ".", "recursive": true}'
        if "read_file" in allowed and state_obj.facts.files_read:
            return "read one more high-signal file or call report_findings"
    if phase_name == "PLANNING" and "report_plan" in allowed:
        return "call report_plan as soon as the file-level steps are clear"
    if phase_name == "PATCHING":
        if step_files and "read_file" in allowed:
            return 'call read_file for "%s" before writing' % step_files[0]
        if "write_file" in allowed:
            return "call write_file with the full updated file content"
    if phase_name == "VERIFYING":
        if state_obj.facts.files_modified:
            return 'read_file for "%s" and then decide report_done vs report_blocked' % state_obj.facts.files_modified[-1]
        if "report_done" in allowed:
            return "call report_done if the task is complete"
    return "choose the single next action allowed in this phase"
