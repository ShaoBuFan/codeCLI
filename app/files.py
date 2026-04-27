from pathlib import Path

import safety


def list_files(root, relative_path=".", recursive=True, pattern=None, limit=200):
    base_path = safety.resolve_in_root(root, relative_path)
    if not base_path.exists():
        return {"ok": False, "error": "Path does not exist"}
    if base_path.is_file():
        return {"ok": True, "items": [str(base_path.relative_to(root))]}

    items = []
    if pattern:
        if recursive:
            iterator = base_path.rglob(pattern)
        else:
            iterator = base_path.glob(pattern)
    elif recursive:
        iterator = base_path.rglob("*")
    else:
        iterator = base_path.glob("*")

    for path in iterator:
        if len(items) >= limit:
            break
        if path.is_dir():
            continue
        items.append(str(path.relative_to(root)))
    return {"ok": True, "items": items, "truncated": len(items) >= limit}


def read_file(root, relative_path, max_bytes):
    path = safety.resolve_in_root(root, relative_path)
    if not path.exists():
        return {"ok": False, "error": "File does not exist"}
    if not path.is_file():
        return {"ok": False, "error": "Path is not a file"}
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[:max_bytes]
        truncated = True
    else:
        truncated = False
    content = _decode_text(data)
    return {"ok": True, "path": str(path.relative_to(root)), "content": content, "truncated": truncated}


def search_text(root, keyword, relative_path=".", limit=100):
    base_path = safety.resolve_in_root(root, relative_path)
    if not base_path.exists():
        return {"ok": False, "error": "Path does not exist"}

    results = []
    targets = [base_path] if base_path.is_file() else list(base_path.rglob("*"))
    for path in targets:
        if len(results) >= limit:
            break
        if not path.is_file():
            continue
        text = _read_text_with_fallback(path)
        for index, line in enumerate(text.splitlines(), start=1):
            if keyword in line:
                results.append(
                    {
                        "path": str(path.relative_to(root)),
                        "line": index,
                        "text": line.strip(),
                    }
                )
                if len(results) >= limit:
                    break
    return {"ok": True, "results": results, "truncated": len(results) >= limit}


def write_file(root, relative_path, content):
    path = safety.resolve_in_root(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(path.relative_to(root))}


_ENCODING_CHAIN = ("utf-8", "gbk", "latin-1")


def _decode_text(data):
    """Decode bytes using the fallback chain.  latin-1 decodes anything."""
    for enc in _ENCODING_CHAIN:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return ""  # unreachable: latin-1 always succeeds


def _read_text_with_fallback(path):
    """Read a file using the fallback encoding chain."""
    try:
        return _decode_text(path.read_bytes())
    except OSError:
        return ""
