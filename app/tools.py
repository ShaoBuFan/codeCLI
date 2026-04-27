import files
import safety


def _bool(value, default=True):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value) if value is not None else default


def _report_findings(args):
    key_findings = args.get("key_findings")
    relevant_files = args.get("relevant_files")
    if not isinstance(key_findings, list) or not isinstance(relevant_files, list):
        return {"ok": False, "error": "report_findings requires key_findings[] and relevant_files[]"}
    return {"ok": True, "_report": "findings", "key_findings": key_findings,
            "relevant_files": relevant_files, "constraints": args.get("constraints", [])}


def _report_plan(args):
    steps = args.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        return {"ok": False, "error": "report_plan requires steps[] with at least one step"}
    for s in steps:
        if not isinstance(s.get("intent"), str) or not isinstance(s.get("target_files"), list):
            return {"ok": False, "error": "each step needs intent (str) and target_files (list)"}
    return {"ok": True, "_report": "plan", "steps": steps}


def _report_blocked(args):
    reason = args.get("reason_type", "other")
    valid = {"file_not_found", "dependency_conflict", "test_failure",
             "ambiguous_requirement", "permission_denied", "other"}
    if reason not in valid:
        reason = "other"
    return {"ok": False, "_report": "blocked",
            "reason_type": reason, "detail": args.get("detail", ""),
            "suggested_action": args.get("suggested_action", "retry")}


def _report_done(args):
    summary = args.get("summary", "task completed")
    return {"ok": True, "_report": "done", "summary": summary}


# Phase-agnostic tools (file I/O) and report tools
_ALL_TOOLS = {
    "list_files", "read_file", "search_text", "write_file",
    "report_findings", "report_plan", "report_blocked", "report_done",
}


def is_valid_tool(tool_name, allowed):
    return tool_name in _ALL_TOOLS and (not allowed or tool_name in allowed)


def run_tool(tool_name, arguments, settings):
    # --- report tools (no filesystem access needed) ---
    if tool_name == "report_findings":
        return _report_findings(arguments)
    if tool_name == "report_plan":
        return _report_plan(arguments)
    if tool_name == "report_blocked":
        return _report_blocked(arguments)
    if tool_name == "report_done":
        return _report_done(arguments)

    # --- file tools ---
    root = settings["workdir"]

    if tool_name == "list_files":
        return files.list_files(
            root=root,
            relative_path=arguments.get("path", "."),
            recursive=_bool(arguments.get("recursive", True)),
            pattern=arguments.get("pattern"),
        )
    if tool_name == "read_file":
        return files.read_file(
            root=root,
            relative_path=arguments.get("path", ""),
            max_bytes=settings["max_file_bytes"],
        )
    if tool_name == "search_text":
        return files.search_text(
            root=root,
            keyword=arguments.get("keyword", ""),
            relative_path=arguments.get("path", "."),
        )
    if tool_name == "write_file":
        path = arguments.get("path", "")
        if not safety.confirm_action("Write file %s ? [y/N]: " % path):
            return {"ok": False, "error": "User rejected write"}
        return files.write_file(
            root=root,
            relative_path=path,
            content=arguments.get("content", ""),
        )

    return {"ok": False, "error": "Unknown tool: %s" % tool_name}
