from pathlib import Path


def resolve_in_root(root, target):
    root_path = Path(root).resolve()
    candidate = (root_path / target).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError:
        raise ValueError("Path escapes the project root: %s" % target)
    return candidate


def confirm_action(prompt):
    answer = input(prompt).strip().lower()
    return answer in ("y", "yes")
