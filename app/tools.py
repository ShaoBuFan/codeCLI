import files
import safety
import shell


def _bool(value, default=True):
    if isinstance(value, bool):
        return value
    return default


def run_tool(tool_name, arguments, settings):
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
    if tool_name == "run_shell":
        command = arguments.get("command", "")
        if not safety.confirm_action("Run shell command `%s` ? [y/N]: " % command):
            return {"ok": False, "error": "User rejected command"}
        return shell.run_shell_command(
            command=command,
            workdir=root,
            git_bash_path=settings["git_bash_path"],
            timeout=settings["llm_timeout"],
            max_output_chars=settings["max_shell_output_chars"],
        )
    return {"ok": False, "error": "Unknown tool: %s" % tool_name}


