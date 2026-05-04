"""Phase transitions, tool whitelists, and phase-specific hints.

All phase knowledge lives here — no other module duplicates transition rules,
recommended-action heuristics, or per-phase hint strings.
"""

from state import AgentState, Phase

# ---------------------------------------------------------------------------
# Per-phase tool whitelist
# ---------------------------------------------------------------------------

PHASE_TOOLS: dict[Phase, list[str]] = {
    Phase.IDLE:      [],
    Phase.EXPLORING: ["list_files", "read_file", "search_text", "report_findings"],
    Phase.PLANNING:  ["read_file", "report_plan"],
    Phase.PATCHING:  ["read_file", "write_file", "apply_diff", "report_blocked"],
    Phase.VERIFYING: ["read_file", "search_text", "report_done", "report_blocked"],
    Phase.DONE:      [],
    Phase.FAILED:    [],
}

# ---------------------------------------------------------------------------
# Phase transitions
# ---------------------------------------------------------------------------

def get_next_phase(state: AgentState) -> Phase:
    """Evaluate transition conditions and return the next phase."""
    current = state.phase

    # IDLE → EXPLORING: always
    if current == Phase.IDLE:
        return Phase.EXPLORING

    # EXPLORING → PLANNING: findings reported
    if current == Phase.EXPLORING:
        if state.facts.key_findings:
            return Phase.PLANNING

    # PLANNING → PATCHING: steps are generated
    if current == Phase.PLANNING:
        if state.steps:
            return Phase.PATCHING

    # PATCHING → VERIFYING: all steps done
    if current == Phase.PATCHING:
        if state.all_steps_done():
            return Phase.VERIFYING
        # Any step failed with retry >= 3 → FAILED
        step = state.current_step()
        if step and step.status == "failed" and step.retry_count >= 3:
            return Phase.FAILED

    # VERIFYING → DONE: report_done was called (phase already set to DONE by
    # apply_report_result via transition_on_report, so this stays as-is).
    # DONE and FAILED are terminal.

    return current


def transition_on_report(report_type: str) -> Phase | None:
    """Return the target phase for a report-result, or None if no direct transition."""
    if report_type == "done":
        return Phase.DONE
    return None


# ---------------------------------------------------------------------------
# Phase-specific instructions injected into the system prompt
# ---------------------------------------------------------------------------

PHASE_INSTRUCTIONS: dict[Phase, str] = {
    Phase.IDLE: "",
    Phase.EXPLORING:
        "You are in the EXPLORING phase. Read relevant files to understand "
        "the codebase. When you have enough information, you MUST call "
        "report_findings — it is the ONLY way to advance to the next phase. "
        "Do not give a natural-language completion reply yet; call report_findings first.",
    Phase.PLANNING:
        "You are in the PLANNING phase. Based on the findings, create a "
        "file-level execution plan. You MUST call report_plan with your "
        "step list — this is the ONLY way to advance. Do not write files "
        "or give a natural-language completion reply yet.",
    Phase.PATCHING:
        "You are in the PATCHING phase. Execute the current step shown "
        "in the task state. Read the target file first, then write the "
        "change or apply a diff. If you hit an obstacle, call report_blocked.",
    Phase.VERIFYING:
        "You are in the VERIFYING phase. Check that the changes are "
        "correct. Read the modified files, search for issues. Call "
        "report_done when satisfied, or report_blocked if problems remain.",
    Phase.DONE:   "",
    Phase.FAILED: "",
}

# ---------------------------------------------------------------------------
# Recommended action heuristics  (formerly in orchestrator.py)
# ---------------------------------------------------------------------------

def get_recommended_action(phase: Phase, state: AgentState, allowed: list[str]) -> str:
    """Return a short hint about what the model should do next."""
    phase_name = phase.value
    step = state.current_step()
    step_files = step.target_files if step else []

    if phase_name == "EXPLORING":
        if state.facts.key_findings:
            return "call report_findings now"
        if "list_files" in allowed and not state.facts.files_read:
            return 'call list_files with {"path": ".", "recursive": true}'
        if "read_file" in allowed and state.facts.files_read:
            return "read one more high-signal file or call report_findings"
    if phase_name == "PLANNING" and "report_plan" in allowed:
        return "call report_plan as soon as the file-level steps are clear"
    if phase_name == "PATCHING":
        if step_files and "read_file" in allowed:
            return 'call read_file for "%s" before writing' % step_files[0]
        if "write_file" in allowed or "apply_diff" in allowed:
            return "call write_file or apply_diff with the change"
    if phase_name == "VERIFYING":
        if state.facts.files_modified:
            return 'read_file for "%s" and then decide report_done vs report_blocked' % state.facts.files_modified[-1]
        if "report_done" in allowed:
            return "call report_done if the task is complete"
    return "choose the single next action allowed in this phase"


# ---------------------------------------------------------------------------
# Blocked-text-reply hints  (formerly in orchestrator.py)
# ---------------------------------------------------------------------------

def get_blocked_text_reply_hint(phase: Phase) -> str:
    """Return a hint explaining why a plain-text reply was blocked in this phase."""
    hints = {
        Phase.EXPLORING:
            "You cannot give a plain text completion reply yet (phase: EXPLORING). "
            "Call report_findings to record what you learned.",
        Phase.PLANNING:
            "You cannot give a plain text completion reply yet (phase: PLANNING). "
            "Call report_plan with your step list to advance.",
        Phase.PATCHING:
            "You cannot give a plain text completion reply yet (phase: PATCHING). "
            "Call write_file or apply_diff to create/modify files — "
            "code in a plain text reply is not saved to disk.",
    }
    return hints.get(phase, "Use the available tools to complete the current step.")


# ---------------------------------------------------------------------------
# Tool-result next-hint  (formerly in protocol.py)
# ---------------------------------------------------------------------------

def get_next_hint(tool_name: str, result: dict) -> str:
    """Return a short next-action hint based on the tool result."""
    if result.get("_report") == "findings":
        return "move_toward_planning"
    if result.get("_report") == "plan":
        return "move_toward_patching"
    if result.get("_report") == "done":
        return "task_complete"
    if not result.get("ok"):
        return "inspect_error_or_report_blocked"
    if tool_name == "list_files":
        return "read_relevant_files_or_report_findings"
    if tool_name == "read_file":
        return "decide_between_next_read_or_phase_report"
    if tool_name in ("write_file", "apply_diff"):
        return "review_changes_and_continue"
    return "choose_next_action"
