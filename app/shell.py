import locale
import subprocess
from pathlib import Path

import safety


def _safe_decode(data):
    """Decode bytes to str, trying UTF-8 first with GBK fallback."""
    if not data:
        return ""
    for enc in ("utf-8", locale.getpreferredencoding(), "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _shell_flag(executable):
    """Return the command flag for the given shell executable."""
    name = Path(executable).stem.lower()
    if name == "cmd":
        return "/c"
    if name in ("bash", "sh", "zsh", "fish"):
        return "-c"
    return "-c"


def run_shell_command(command, workdir, git_bash_path, timeout, max_output_chars):
    if safety.is_dangerous_command(command):
        return {"ok": False, "error": "Blocked dangerous command"}

    executable = git_bash_path
    if executable and executable != "bash":
        executable = str(Path(executable))

    flag = _shell_flag(executable)

    try:
        completed = subprocess.run(
            [executable, flag, command],
            cwd=workdir,
            capture_output=True,
            text=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "Shell executable not found: %s" % git_bash_path,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "Shell command timed out after %s seconds" % timeout,
        }

    stdout = _safe_decode(completed.stdout)
    stderr = _safe_decode(completed.stderr)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    if len(combined) > max_output_chars:
        combined = combined[:max_output_chars]
        truncated = True
    else:
        truncated = False
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": combined,
        "truncated": truncated,
    }
