"""Phase transitions and per-phase tool whitelists."""

from state import AgentState, Phase

# The standard file tools.
_FILE_TOOLS = ["list_files", "read_file", "search_text", "write_file"]

# Report tools (structured state updates, not I/O).
_REPORT_TOOLS = [
    "report_findings",   # EXPLORING → state.facts
    "report_plan",       # PLANNING  → state.steps
    "report_blocked",    # any       → fail current step with structured reason
    "report_done",       # VERIFYING → mark task complete
]

# ---------------------------------------------------------------------------
# Per-phase tool whitelist
# ---------------------------------------------------------------------------

PHASE_TOOLS: dict[Phase, list[str]] = {
    Phase.IDLE:      [],
    Phase.EXPLORING: ["list_files", "read_file", "search_text", "report_findings"],
    Phase.PLANNING:  ["read_file", "report_plan"],
    Phase.PATCHING:  ["read_file", "write_file", "report_blocked"],
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

    # EXPLORING → PLANNING: files have been read and findings reported
    if current == Phase.EXPLORING:
        if state.facts.files_read and state.facts.key_findings:
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

    # VERIFYING → DONE: handled by report_done setting phase directly.
    # DONE is terminal — the orchestrator loop exits naturally.

    return current


# ---------------------------------------------------------------------------
# Phase-specific instructions injected into the system prompt
# ---------------------------------------------------------------------------

PHASE_INSTRUCTIONS: dict[Phase, str] = {
    Phase.IDLE: "",
    Phase.EXPLORING:
        "You are in the EXPLORING phase. Read relevant files to understand "
        "the codebase. When you have enough information, call report_findings "
        "with your key findings, relevant files, and any constraints.",
    Phase.PLANNING:
        "You are in the PLANNING phase. Based on the findings, create a "
        "file-level execution plan. Call report_plan with a list of steps, "
        "each with an intent and target files.",
    Phase.PATCHING:
        "You are in the PATCHING phase. Execute the current step. Read the "
        "target file first, then write the change. If you hit an obstacle, "
        "call report_blocked with a structured reason.",
    Phase.VERIFYING:
        "You are in the VERIFYING phase. Check that the changes are correct. "
        "Read the modified files, search for issues. Call report_done when "
        "satisfied, or report_blocked if problems remain.",
    Phase.DONE:   "",
    Phase.FAILED: "",
}
