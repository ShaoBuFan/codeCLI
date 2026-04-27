"""Project initialization: auto-explore and generate PROJECT.md.

Orchestrates file discovery, scoring, and model-driven summarization.
Scoring internals are private — only `run_init` is public.
"""

import os

import agent
import files

# ---------------------------------------------------------------------------
# Scoring tables
# ---------------------------------------------------------------------------

_PRIORITY = {
    "Makefile", "Dockerfile", "docker-compose*",
    "README*", "LICENSE*", "CHANGELOG*", "CONTRIBUTING*",
    "pyproject.toml", "setup.py", "setup.cfg", "requirements*.txt",
    "package.json", "tsconfig.json", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "CMakeLists.txt",
    "main.*", "app.*", "index.*", "cli.*", "server.*", "run.*",
    "*.yaml", "*.yml", "*.toml", "*.cfg", "*.ini", "*.conf",
    "*.env*", ".gitignore", ".editorconfig",
}

_DEPRIORITY = {
    "__pycache__", "*.pyc", "*.pyo", "*.so", "*.dll", "*.dylib",
    "*.log", "*.lock", "*.min.js", "*.min.css", "*.map",
    "package-lock.json", "yarn.lock", "Cargo.lock", "go.sum",
    "poetry.lock", "Pipfile.lock",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.svg",
    "*.woff", "*.woff2", "*.ttf", "*.eot",
    "*.mp3", "*.mp4", "*.avi", "*.mov",
    "*.zip", "*.tar", "*.gz", "*.bz2", "*.7z",
    "*.pdf", "*.doc", "*.docx", "*.xls", "*.xlsx",
}

_NOISE_DIRS = {
    "logs", "node_modules", "__pycache__", ".git", ".svn",
    "dist", "build", "target", ".next", ".nuxt",
    "vendor", "bower_components", ".cache", ".vscode", ".idea",
    "coverage", ".pytest_cache", ".mypy_cache", ".tox",
    "venv", ".venv", "env", ".env", "site-packages",
}

# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def _match_name(name, pattern):
    if pattern.startswith("*.") and name.endswith(pattern[1:]):
        return True
    if pattern.endswith("*") and name.startswith(pattern[:-1]):
        return True
    return name == pattern


def _score(rel_path, size):
    """Assign a relevance score to a file.  Lower = read sooner.

    Base score is the file size in bytes.  Priority patterns divide,
    depriority and noise directories multiply (with a high floor).
    """
    name = rel_path.split("/")[-1].split("\\")[-1]
    normalized = rel_path.replace("\\", "/")
    segments = set(normalized.split("/")[:-1])
    score = max(size, 1)

    for pat in _PRIORITY:
        if _match_name(name, pat):
            score = max(score // 3, 1)
            break

    for pat in _DEPRIORITY:
        if _match_name(name, pat):
            score = score * 30 + 100_000
            break

    if segments & _NOISE_DIRS and score < 100_000:
        score = max(score * 50, 50_000)

    if normalized.count("/") == 0:
        score = score * 2 // 3

    return score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(settings, payload, client):
    """Explore *workdir* and generate PROJECT.md through the model.

    Lists all files, scores them, reads the best candidates up to a
    300 KB budget, then hands everything to the agent loop so the
    model can ask for more detail and finally write the output file.
    """
    root = payload["workdir"]
    per_file_limit = settings["max_file_bytes"]
    total_budget = 300 * 1024

    # 1. List
    result = files.list_files(root=root, recursive=True)
    if not result.get("ok"):
        print("error:", result.get("error", "list failed"))
        return

    all_files = result.get("items", [])
    truncated = result.get("truncated", False)
    print("scanning %d files%s..." % (len(all_files), "+" if truncated else ""))

    # 2. Stat + score → sort
    entries = []
    for rel in all_files:
        try:
            size = os.path.getsize(os.path.join(root, rel))
        except OSError:
            size = 0
        entries.append((rel, size, _score(rel, size)))
    entries.sort(key=lambda e: e[2])

    # 3. Read best-first until budget
    context_parts = []
    files_read = []
    total = 0

    for rel, size, _sc in entries:
        if total >= total_budget:
            break
        limit = min(per_file_limit, total_budget - total)
        r = files.read_file(root=root, relative_path=rel, max_bytes=limit)
        if not r.get("ok"):
            continue
        content = r["content"]
        if "\x00" in content:
            continue
        context_parts.append("--- %s (%dB) ---\n%s\n" % (rel, size, content))
        files_read.append(rel)
        total += len(content)

    print("read %d files (%d KB)" % (len(files_read), total // 1024))

    # 4. Build file listing
    listing = "Project files (%d total, %d read):\n" % (len(all_files), len(files_read))
    for rel, size, _sc in entries[:60]:
        mark = "  *" if rel in files_read else "   "
        listing += "%s %s  (%dB)\n" % (mark, rel, size)
    if len(entries) > 60:
        listing += "   ... and %d more\n" % (len(entries) - 60)

    # 5. Hand off to model
    print("analyzing...")
    prompt = listing + "\n" + "\n".join(context_parts)
    prompt += "\n\nCreate PROJECT.md covering: project summary, architecture, key files, build/run/test commands, and conventions. Use write_file to save it."
    answer = agent.handle_user_message(client, settings, payload, prompt)
    print()
    print(answer)
