"""Embedded tool-call parsing and content extraction."""

import json
import re
import time
from pathlib import Path

import phase as ph

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)


def strip_code_fence(text):
    """Remove markdown code fences from model output."""
    value = (text or "").strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return value


def decode_escaped_text(text):
    """Replace escaped newlines, tabs, and quotes with actual characters."""
    if not isinstance(text, str):
        return text
    if "\\n" in text:
        text = text.replace("\\n", "\n")
    if "\\t" in text:
        text = text.replace("\\t", "\t")
    if '\\"' in text:
        text = text.replace('\\"', '"')
    return text


def parse_model_output(raw_text):
    """Parse model output into natural language plus zero or more embedded tool calls."""
    text = strip_code_fence(raw_text)
    if not text:
        return None, "empty_output"

    matches = list(_TOOL_CALL_RE.finditer(text))
    if not matches:
        if "<tool_call>" in text.lower() or "</tool_call>" in text.lower():
            return None, "unclosed_tool_call"
        return {"type": "text", "content": text}, None

    tool_calls = []
    for match in matches:
        try:
            parsed = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            return None, "json_decode_error"

        if not isinstance(parsed, dict):
            return None, "invalid_structure"

        if parsed.get("type") != "tool_call":
            return None, "unknown_type"

        tool = parsed.get("tool", "")
        arguments = parsed.get("arguments", {})
        if not isinstance(tool, str) or not tool:
            return None, "empty_tool_name"
        if not isinstance(arguments, dict):
            return None, "invalid_arguments"
        tool_calls.append({"tool": tool, "arguments": arguments})

    content = _strip_tool_call_blocks(text).strip()
    return {"type": "tool_calls", "content": content, "tool_calls": tool_calls}, None


def extract_text_content(parsed):
    """Extract clean answer text from a parsed natural-language response."""
    if not isinstance(parsed, dict) or parsed.get("type") != "text":
        return ""
    return decode_escaped_text(parsed.get("content", ""))


def _strip_tool_call_blocks(text):
    return _TOOL_CALL_RE.sub("", text)


def log_malformed_output(logs_dir, stage, raw_output, parse_result):
    """Append a malformed-output entry to the debug log."""
    try:
        log_path = Path(logs_dir) / "malformed_outputs.jsonl"
        entry = {
            "timestamp": int(time.time()),
            "stage": stage,
            "raw_output": raw_output,
            "parsed_preview": str(parse_result)[:1000],
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def format_tool_result_message(tool_name, arguments, result):
    """Format a tool result into the interleaved message for the model."""
    next_hint = ph.get_next_hint(tool_name, result)
    payload = {
        "tool": tool_name,
        "arguments": arguments,
        "ok": bool(result.get("ok")),
        "error": result.get("error", ""),
        "report": result.get("_report", ""),
        "result": result,
        "next_hint": next_hint,
    }
    return "--- tool call result ---\n%s" % json.dumps(payload, ensure_ascii=False, sort_keys=True)
