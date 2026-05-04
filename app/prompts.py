import json

import tools


SYSTEM_PROMPT = """You are a local coding assistant running inside a CLI.

You help users by reading their project files and answering questions.

Protocol:
  1. Normal answers should be plain natural language.
  2. Only tool calls must be structured.
  3. To call a tool, embed one or more <tool_call>...</tool_call> blocks in your reply.
  4. Natural language before, between, or after tool_call blocks is allowed.
  5. Inside each tag, provide JSON like:
     {"type":"tool_call","tool":"TOOL_NAME","arguments":{...}}

Tool call cycle:
  - You request a tool → system executes it → you receive the result as a user message
  - Read the result, then decide: make another tool call or return a natural-language reply
  - Never repeat the same tool call with the same arguments

When analyzing a project, explore all directories — list root contents first, then for
each subdirectory call list_files(path="subdir") to see its contents. Read all key
files (configs, entry points, core logic). Only return a natural-language reply after you have
examined every major directory and understand the full project structure.

Language: match the user's language. If unclear, default to Simplified Chinese.
Keep code, file paths, commands, JSON keys, and API field names in their original form."""


MATTERMOST_PROTOCOL_PROMPT = """You are using a text-only channel without native tool calling.

Output contract:
1. Normal answers can be plain natural language.
2. Only tool calls must be structured.
3. To call a tool, embed one or more <tool_call>...</tool_call> blocks in your reply.
4. Natural language before, between, or after tool_call blocks is allowed.
5. Inside each tag, provide one JSON object:
   {"type":"tool_call","tool":"...","arguments":{...}}
6. Do not wrap tool calls in markdown fences.
7. Think silently, then output natural language and tool_call blocks as needed."""


def is_mattermost(settings):
    return settings.get("llm_provider") == "mattermost"


def build_base_prompt(settings):
    if is_mattermost(settings):
        return MATTERMOST_PROTOCOL_PROMPT
    return SYSTEM_PROMPT


def build_tool_prompt(provider, allowed, recommended_action=""):
    if provider == "mattermost":
        return _build_mattermost_tool_prompt(allowed, recommended_action)
    return _build_standard_tool_prompt()


def _build_standard_tool_prompt():
    lines = ["Available tools:", ""]
    for idx, (name, schema) in enumerate(tools.TOOL_SCHEMAS.items(), start=1):
        lines.append("%d. %s — %s" % (idx, name, schema["description"]))
        sig = _tool_signature(name)
        lines.append("   %s" % sig)
        lines.append("")
    lines.append("Permission notes:")
    lines.append("- list_files, read_file, search_text: no confirmation needed, use freely")
    lines.append("- write_file, apply_diff: user must confirm with y/N, prefer read-only tools when possible")
    lines.append("")
    lines.append("You may include multiple tool calls in one response if they are clearly needed in sequence.")
    lines.append("Each tool call must use this exact format:")
    lines.append('<tool_call>{"type":"tool_call","tool":"...","arguments":{...}}</tool_call>')
    lines.append("")
    lines.append("When you receive a tool result, it will appear as a user message in this format:")
    lines.append("--- tool call result ---")
    lines.append("<json payload>")
    lines.append("")
    lines.append("Read it as execution output from the local environment.")
    return "\n".join(lines)


def _build_mattermost_tool_prompt(allowed, recommended_action):
    allowed_text = ", ".join(allowed) if allowed else "none"
    lines = [
        "Tool policy for this turn:",
        "Allowed now: %s" % allowed_text,
        "Forbidden now: any tool not listed above.",
        "You may include multiple tool calls in one response when needed.",
        'Each tool call must use: <tool_call>{"type":"tool_call","tool":"...","arguments":{...}}</tool_call>',
    ]
    if recommended_action:
        lines.append("Recommended next action: %s" % recommended_action)
    lines.append("")
    lines.append("Tool argument reference:")
    for name in allowed:
        lines.append("- %s %s" % (name, _tool_signature(name)))
    return "\n".join(lines)


def _tool_signature(name):
    schema = tools.TOOL_SCHEMAS.get(name)
    if not schema:
        return ""
    params = schema["parameters"]
    param_str = ", ".join(
        "%s:%s" % (k, json.dumps(v, ensure_ascii=False))
        for k, v in params.items()
    )
    return "arguments: {%s}" % param_str


def build_phase_guidance(provider, phase, allowed, current_step, files_modified):
    if provider != "mattermost":
        return ""

    phase_name = getattr(phase, "value", str(phase))
    lines = [
        "Decision rules for this turn:",
        "Current phase: %s" % phase_name,
        "Current step: %s" % (current_step or "none"),
    ]

    if phase_name == "EXPLORING":
        lines.extend([
            "If you still need project structure, call list_files.",
            "If you already know the key architecture facts, call report_findings instead of exploring more.",
            "Do not give a natural-language completion reply in this phase.",
        ])
    elif phase_name == "PLANNING":
        lines.extend([
            "Use read_file only if a target file is still unclear.",
            "As soon as the file-level plan is clear, call report_plan.",
            "Do not give a natural-language completion reply in this phase.",
        ])
    elif phase_name == "PATCHING":
        lines.extend([
            "If a target file has not been inspected in this phase, read_file first.",
            "If the change is ready, call write_file or apply_diff.",
            "If you cannot safely continue, call report_blocked.",
            "Do not give a natural-language completion reply in this phase.",
        ])
    elif phase_name == "VERIFYING":
        lines.extend([
            "Review modified files first: %s." % (", ".join(files_modified) or "none"),
            "If everything looks correct, call report_done.",
            "If issues remain, call report_blocked.",
        ])
    else:
        lines.append("Choose the single next action that best matches the current phase.")

    lines.append("Allowed tools reminder: %s" % (", ".join(allowed) or "none"))
    return "\n".join(lines)


def build_retry_prompt(retry_number, error=None, provider="openai_compatible"):
    msg = "Your previous response did not follow the required tool-call contract."

    hints = {
        "json_decode_error": " The JSON inside a <tool_call> block was not valid.",
        "empty_tool_name": ' It had type "tool_call" but "tool" was empty.',
        "invalid_arguments": ' It had type "tool_call" but "arguments" was not a JSON object.',
        "unknown_type": ' The "type" field inside <tool_call> must be "tool_call".',
        "empty_output": " The response was empty.",
        "invalid_structure": " The response was not a JSON object.",
        "unclosed_tool_call": " A <tool_call> tag was not properly closed.",
    }
    if error and error in hints:
        msg += hints[error]

    if provider == "mattermost":
        return msg + """

Retry now with a stricter format.
1. If you need tools, use one or more <tool_call>...</tool_call> blocks.
2. Put exactly one JSON object inside each tag.
3. Natural language is allowed outside the tags.
4. If you do not need a tool, answer in plain natural language.
5. Prefer the recommended next action when it is available.
"""

    if retry_number >= 2:
        return msg + """

Retry now with a simpler response.
1. If you need a tool, output one or more valid <tool_call>...</tool_call> blocks.
2. If you do not need a tool, give a short plain-text answer.
3. Do not use markdown fences around tool calls.
4. Put valid JSON inside each tool_call tag.
"""
    return msg + """

Retry now and obey these rules:
1. If you need a tool, output one or more valid <tool_call>...</tool_call> blocks.
2. Put valid JSON inside each tag.
3. If you do not need a tool, answer in plain natural language.
4. Natural language outside the tags is allowed.
"""


def build_correction_prompt(provider, phase, issue, allowed):
    phase_name = getattr(phase, "value", str(phase))
    allowed_text = ", ".join(allowed) or "none"

    if provider != "mattermost":
        return ""

    prompts = {
        "premature_final":
            "You cannot give a natural-language completion reply in phase %s. Allowed tools now: %s. Use one or more tool_call blocks."
            % (phase_name, allowed_text),
        "invalid_tool":
            "You called a forbidden tool in phase %s. Allowed tools now: %s. Use corrected tool_call blocks only."
            % (phase_name, allowed_text),
        "repeated_call":
            "You repeated the same tool call. Choose a different next action or report_blocked. Use different tool_call blocks.",
    }
    return prompts.get(issue, "")
