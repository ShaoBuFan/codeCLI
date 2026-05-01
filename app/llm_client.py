import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def _mask_headers(headers):
    masked = {}
    sensitive = {"authorization", "cookie", "set-cookie", "x-api-key", "api-key", "proxy-authorization"}
    for key, value in (headers or {}).items():
        masked[key] = "***" if key.lower() in sensitive else value
    return masked


def _truncate_text(value, limit=4000):
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _write_debug_log(settings, stage, data):
    if not settings.get("llm_debug"):
        return
    try:
        log_path = Path(settings["logs_dir"]) / "llm_debug.jsonl"
        entry = {"timestamp": int(time.time()), "stage": stage, "data": data}
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _http_post(url, payload_text, headers, timeout):
    """POST JSON payload, return decoded response body."""
    request = urllib.request.Request(
        url, data=payload_text.encode("utf-8"), method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("Provider HTTP %s: %s" % (exc.code, body))
    except urllib.error.URLError as exc:
        raise RuntimeError("Provider connection failed: %s" % exc)


class BaseLLMClient:
    def chat(self, messages):
        raise NotImplementedError


class OpenAICompatibleClient(BaseLLMClient):
    def __init__(self, api_key, base_url, model, timeout, settings, headers=None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.settings = settings
        self.headers = headers or {}

    def _build_payload(self, messages):
        return {"model": self.model, "messages": messages, "temperature": 0.2}

    def chat(self, messages):
        if not self.api_key:
            raise RuntimeError("Missing API key")

        headers = {"Content-Type": "application/json", "Authorization": "Bearer %s" % self.api_key}
        headers.update(self.headers)

        payload_text = json.dumps(self._build_payload(messages), ensure_ascii=False)
        _write_debug_log(self.settings, "openai_compatible_request", {
            "url": self.base_url, "headers": _mask_headers(headers), "body": _truncate_text(payload_text),
        })

        body = _http_post(self.base_url, payload_text, headers, self.timeout)
        _write_debug_log(self.settings, "openai_compatible_response", {
            "url": self.base_url, "body": _truncate_text(body),
        })

        try:
            return json.loads(body)["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Unexpected OpenAI-compatible response: %s" % body)


class MattermostClient(BaseLLMClient):
    """Client for Mattermost ChatGPT plugin.

    Sends messages as a JSON array directly (same format as internal messages).
    Content-Type text/plain. Auth headers constructed from dedicated config fields.
    """

    def __init__(self, base_url, model_key, access_team, mmauth_token, mmuser_id, csrf_token, timeout, settings):
        self.base_url = base_url
        self.model_key = model_key
        self.access_team = access_team
        self.timeout = timeout
        self.settings = settings
        self._headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "X-CSRF-Token": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": "MMAUTHTOKEN=%s; MMUSERID=%s; MMCSRF=%s" % (mmauth_token, mmuser_id, csrf_token),
        }

    def _url(self):
        t = int(time.time() * 1000)
        return "%s/%s?requestTime=%s&accessTeam=%s" % (
            self.base_url.rstrip("/"), self.model_key, t, self.access_team,
        )

    def chat(self, messages):
        payload_text = json.dumps(messages, ensure_ascii=False)

        _write_debug_log(self.settings, "mattermost_request", {
            "url": self._url(), "headers": _mask_headers(self._headers), "body": _truncate_text(payload_text),
        })

        body = _http_post(self._url(), payload_text, self._headers, self.timeout)
        _write_debug_log(self.settings, "mattermost_response", {
            "url": self._url(), "body": _truncate_text(body),
        })

        try:
            return json.loads(body)["reqMessage"]
        except (KeyError, json.JSONDecodeError, TypeError):
            raise RuntimeError("Unexpected response (missing reqMessage): %s" % _truncate_text(body, 500))


def build_client(settings):
    provider = settings["llm_provider"]

    if provider == "openai_compatible":
        return OpenAICompatibleClient(
            api_key=settings["llm_api_key"], base_url=settings["llm_base_url"],
            model=settings["llm_model"], timeout=settings["llm_timeout"],
            settings=settings, headers=settings.get("llm_headers") or {},
        )

    if provider == "mattermost":
        return MattermostClient(
            base_url=settings["llm_base_url"], model_key=settings["llm_model_key"],
            access_team=settings.get("access_team", ""),
            mmauth_token=settings.get("mmauth_token", ""),
            mmuser_id=settings.get("mmuser_id", ""),
            csrf_token=settings.get("csrf_token", ""),
            timeout=settings["llm_timeout"], settings=settings,
        )

    raise RuntimeError("Unsupported llm_provider: %s" % provider)
