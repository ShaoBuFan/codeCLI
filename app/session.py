import json
import time
import uuid
from pathlib import Path

import config


def new_session_id():
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]


def session_path(session_id):
    return config.sessions_dir() / (session_id + ".json")


def create_session(workdir):
    session_id = new_session_id()
    payload = {
        "session_id": session_id,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "workdir": str(Path(workdir).resolve()),
        "messages": [],
        "tool_calls": [],
    }
    save_session(payload)
    return payload


def load_session(session_id):
    path = session_path(session_id)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Failed to load session %s: %s" % (session_id, exc))


def save_session(payload):
    payload["updated_at"] = int(time.time())
    path = session_path(payload["session_id"])
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def append_message(payload, role, content):
    payload["messages"].append({"role": role, "content": content})


def append_tool_call(payload, tool_name, arguments, result):
    payload["tool_calls"].append(
        {
            "tool": tool_name,
            "arguments": arguments,
            "result": result,
        }
    )


def list_sessions():
    items = []
    for path in sorted(config.sessions_dir().glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            items.append(
                {
                    "session_id": payload.get("session_id", path.stem),
                    "updated_at": payload.get("updated_at", 0),
                    "workdir": payload.get("workdir", ""),
                }
            )
        except (OSError, json.JSONDecodeError):
            items.append({"session_id": path.stem, "updated_at": 0, "workdir": ""})
    items.sort(key=lambda item: item["updated_at"], reverse=True)
    return items
