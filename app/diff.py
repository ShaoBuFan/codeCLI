"""Zero-dependency unified-diff parser and applicator."""

import re


class DiffError(Exception):
    def __init__(self, message, hunk_index=-1, expected_context="", actual_context=""):
        super().__init__(message)
        self.hunk_index = hunk_index
        self.expected_context = expected_context
        self.actual_context = actual_context


_HUNK_HEADER_RE = re.compile(
    r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$'
)


def apply_diff(file_content, diff_text):
    """Apply unified diff text to *file_content* and return the new content.

    Raises ``DiffError`` when a hunk cannot be applied.
    """
    line_ending = _detect_line_ending(file_content)
    lines = file_content.replace("\r\n", "\n").splitlines()
    hunks = _parse_hunks(diff_text)

    offset = 0
    for i, hunk in enumerate(hunks):
        try:
            lines, delta = _apply_hunk(lines, hunk, offset)
        except _HunkMismatch as exc:
            raise DiffError(
                "hunk %d failed at line %d" % (i + 1, hunk["old_start"] + offset),
                hunk_index=i,
                expected_context="\n".join(exc.expected[:5]),
                actual_context="\n".join(lines[max(0, exc.pos - 3):exc.pos + 3]),
            ) from exc
        offset += delta

    content = "\n".join(lines)
    # Preserve trailing newline: if original had one (or was empty, implying
    # a new file being created), keep it. Only strip if original was non-empty
    # and explicitly lacked a trailing newline.
    if file_content and not file_content.endswith("\n") and not file_content.endswith("\r\n"):
        pass  # original lacked trailing newline → keep it that way
    else:
        content += "\n"
    if line_ending == "\r\n":
        content = content.replace("\n", "\r\n")
    return content


# ---------------------------------------------------------------------------
# Hunk parsing
# ---------------------------------------------------------------------------

def _parse_hunks(diff_text):
    hunks = []
    current = None

    for line in diff_text.replace("\r\n", "\n").splitlines():
        if line.startswith("@@") and "@@" in line[2:]:
            if current:
                hunks.append(_finalize_hunk(current))
            m = _HUNK_HEADER_RE.match(line)
            if not m:
                raise DiffError("invalid hunk header: %s" % line)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            current = {
                "old_start": old_start, "old_count": old_count,
                "new_start": new_start, "new_count": new_count,
                "context": m.group(5) or "",
                "old_lines": [], "new_lines": [],
            }
        elif current is not None:
            if line == "\\ No newline at end of file":
                continue
            if line.startswith(" "):
                current["old_lines"].append(line[1:])
                current["new_lines"].append(line[1:])
            elif line.startswith("-"):
                current["old_lines"].append(line[1:])
            elif line.startswith("+"):
                current["new_lines"].append(line[1:])

    if current:
        hunks.append(_finalize_hunk(current))
    return hunks


def _finalize_hunk(hunk):
    hunk["old_count"] = len(hunk["old_lines"])
    hunk["new_count"] = len(hunk["new_lines"])
    return hunk


# ---------------------------------------------------------------------------
# Hunk application
# ---------------------------------------------------------------------------

_FUZZY_WINDOW = 10


class _HunkMismatch(Exception):
    def __init__(self, expected, pos):
        self.expected = expected
        self.pos = pos


def _apply_hunk(lines, hunk, offset):
    old_len = len(hunk["old_lines"])
    # 1-based to 0-based conversion, then apply cumulative offset
    start_pos = hunk["old_start"] - 1 + offset

    # Handle empty-file addition: @@ -0,0 +1,N @@
    if hunk["old_start"] == 0 and old_len == 0:
        if not lines:
            return hunk["new_lines"][:], len(hunk["new_lines"]) - 0
        start_pos = 0
    elif start_pos < 0 or start_pos + old_len > len(lines):
        raise _HunkMismatch(hunk["old_lines"], start_pos)

    expected = hunk["old_lines"]
    actual = lines[start_pos:start_pos + old_len]

    if actual != expected:
        fuzzy = _fuzzy_match(lines, expected, start_pos, _FUZZY_WINDOW)
        if fuzzy is not None:
            start_pos = fuzzy
        else:
            raise _HunkMismatch(expected, start_pos)

    result = list(lines)
    result[start_pos:start_pos + len(hunk["old_lines"])] = hunk["new_lines"]
    delta = len(hunk["new_lines"]) - len(hunk["old_lines"])
    return result, delta


def _fuzzy_match(lines, expected, guess, window):
    for d in range(1, window + 1):
        for delta in (d, -d):
            pos = guess + delta
            if 0 <= pos <= len(lines) - len(expected):
                if lines[pos:pos + len(expected)] == expected:
                    return pos
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_line_ending(content):
    if "\r\n" in content:
        return "\r\n"
    return "\n"


def _render_context(lines, pos, radius=3):
    """Render a snippet of lines around *pos* for error messages."""
    if pos < 0 or pos >= len(lines):
        return "(EOF)"
    lo = max(0, pos - radius)
    hi = min(len(lines), pos + radius)
    return "\n".join(lines[lo:hi])
