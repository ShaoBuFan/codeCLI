from pathlib import Path


DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "del /f /s /q",
    "format ",
    "mkfs",
    "shutdown",
    "reboot",
    "poweroff",
    ":(){:|:&};:",
]


def resolve_in_root(root, target):
    root_path = Path(root).resolve()
    candidate = (root_path / target).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError:
        raise ValueError("Path escapes the project root: %s" % target)
    return candidate


def is_dangerous_command(command):
    lowered = command.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern in lowered:
            return True
    return False


def confirm_action(prompt):
    answer = input(prompt).strip().lower()
    return answer in ("y", "yes")
