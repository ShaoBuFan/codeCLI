"""JSON protocol parsing and content extraction.

Parses model output into structured responses and formats
tool results back into the message stream.
"""

import json
import time
from pathlib import Path


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
    """Parse model output into a structured response dict.

    Returns (parsed_dict, None) on success or (None, error_code) on failure.
    error_code is a short string like "json_decode_error", "missing_content", etc.
    """
    text = strip_code_fence(raw_text)
    if not text:
        return None, "empty_output"

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "json_decode_error"

    if not isinstance(parsed, dict):
        return None, "invalid_structure"

    if parsed.get("type") == "final":
        content = parsed.get("content")
        if isinstance(content, str):
            return {"type": "final", "content": content}, None
        return None, "missing_content"

    if parsed.get("type") == "tool_call":
        tool = parsed.get("tool", "")
        arguments = parsed.get("arguments", {})
        if not isinstance(tool, str) or not tool:
            return None, "empty_tool_name"
        if not isinstance(arguments, dict):
            return None, "invalid_arguments"
        return {"type": "tool_call", "tool": tool, "arguments": arguments}, None

    return None, "unknown_type"


def extract_final_content(parsed):
    """Extract clean answer text from a parsed final response."""
    if not isinstance(parsed, dict) or parsed.get("type") != "final":
        return ""
    return decode_escaped_text(parsed.get("content", ""))


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
    return "--- tool call result ---\ntool: %s\narguments: %s\nresult: %s" % (
        tool_name,
        json.dumps(arguments, ensure_ascii=False, sort_keys=True),
        json.dumps(result, ensure_ascii=False),
    )
