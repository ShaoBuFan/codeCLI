import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def _parse_json_or_empty(text):
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _dict_from_setting(raw_value, fallback_text=""):
    return raw_value if isinstance(raw_value, dict) else _parse_json_or_empty(fallback_text)


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


class GenericJSONClient(BaseLLMClient):
    def __init__(self, base_url, timeout, settings, headers=None, body_template=None, response_path=None):
        self.base_url = base_url
        self.timeout = timeout
        self.settings = settings
        self.headers = headers or {}
        self.body_template = body_template or {}
        self.response_path = response_path or "content"

    def _replace_placeholders(self, value, messages_text, messages):
        if isinstance(value, str):
            return (
                value.replace("{messages_text}", messages_text)
                .replace("{messages_json}", json.dumps(messages, ensure_ascii=False))
            )
        if isinstance(value, dict):
            return {k: self._replace_placeholders(v, messages_text, messages) for k, v in value.items()}
        if isinstance(value, list):
            return [self._replace_placeholders(v, messages_text, messages) for v in value]
        return value

    def _build_payload(self, messages):
        messages_text = "\n\n".join(
            "%s:\n%s" % (m.get("role", "user"), m.get("content", "")) for m in messages
        )
        if not self.body_template:
            return {"messages": messages}
        return self._replace_placeholders(self.body_template, messages_text, messages)

    def _extract_response(self, data):
        current = data
        for part in self.response_path.split("."):
            if isinstance(current, list):
                try:
                    index = int(part)
                except ValueError:
                    raise RuntimeError("Response path expects list index: %s" % part)
                try:
                    current = current[index]
                except IndexError:
                    raise RuntimeError("Response path index out of range: %s" % part)
            elif isinstance(current, dict):
                if part not in current:
                    raise RuntimeError("Response path not found: %s" % self.response_path)
                current = current[part]
            else:
                raise RuntimeError("Cannot traverse response path: %s" % self.response_path)
        if not isinstance(current, str):
            raise RuntimeError("Response path did not resolve to text: %s" % self.response_path)
        return current

    def chat(self, messages):
        payload_text = json.dumps(self._build_payload(messages), ensure_ascii=False)
        headers = {"Content-Type": "application/json"}
        headers.update(self.headers)
        _write_debug_log(self.settings, "generic_json_request", {
            "url": self.base_url, "headers": _mask_headers(headers),
            "body": _truncate_text(payload_text), "response_path": self.response_path,
        })

        body = _http_post(self.base_url, payload_text, headers, self.timeout)
        _write_debug_log(self.settings, "generic_json_response", {
            "url": self.base_url, "body": _truncate_text(body), "response_path": self.response_path,
        })

        return self._extract_response(json.loads(body))


def build_client(settings):
    provider = settings["llm_provider"]
    headers = _dict_from_setting(settings.get("llm_headers"), settings.get("llm_headers_json", ""))

    if provider == "openai_compatible":
        return OpenAICompatibleClient(
            api_key=settings["llm_api_key"], base_url=settings["llm_base_url"],
            model=settings["llm_model"], timeout=settings["llm_timeout"],
            settings=settings, headers=headers,
        )

    if provider == "generic_json":
        body_template = _dict_from_setting(
            settings.get("llm_body_template"), settings.get("llm_body_template_json", ""),
        )
        return GenericJSONClient(
            base_url=settings["llm_base_url"], timeout=settings["llm_timeout"],
            settings=settings, headers=headers,
            body_template=body_template, response_path=settings["llm_response_path"],
        )

    raise RuntimeError("Unsupported llm_provider: %s" % provider)
