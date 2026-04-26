# 对接公司 API 指南

## 项目整体架构

```
用户输入 → main.py → cli.py (REPL)
                   → agent.py (工具调用循环)
                       → messages.py (组装消息列表)
                       → llm_client.py (发 HTTP 请求到 LLM)
                       → protocol.py (解析 LLM 返回的 JSON)
                       → tools.py (执行文件/Shell 操作)
                       → session.py (存储对话历史)
```

关键设计：**所有 LLM 通信走自定义 JSON 协议，不依赖原生 function calling**。

## 协议：Agent 期待 LLM 返回什么

agent 循环要求 LLM 输出**两种 JSON** 之一，且必须是字符串形式嵌在 API 响应的某个字段中：

### 最终回答
```json
{"type": "final", "content": "你的回答文本"}
```

### 工具调用
```json
{"type": "tool_call", "tool": "list_files", "arguments": {"path": ".", "recursive": true}}
```

可用工具列表：`list_files`、`read_file`、`search_text`、`write_file`、`run_shell`

### 工具调用循环流程

1. Agent 发 messages 给 LLM → LLM 返回 `tool_call`
2. Agent 执行工具 → 把结果注入回 messages（role: system）
3. Agent 再次发 messages（含工具结果）给 LLM → LLM 返回 final 或下一个 tool_call
4. 重复直到 max_steps（默认 25）
5. 同一个工具 + 相同参数重复调用会被判定为死循环，直接终止

所以 LLM 每次返回的必须是一个**完整的工具调用指令**或**最终回答**，无法做到多步工具调用一次返回。

## GenericJSONClient 工作原理

```python
# 配置 (local_config.json):
{
  "active_provider": "company",
  "providers": {
    "company": {
      "llm_provider": "generic_json",
      "llm_base_url": "https://your-api",
      "llm_headers": {
        "Authorization": "Bearer ...",
        "Cookie": "..."
      },
      "llm_body_template": {
        "input": "{messages_text}"
      },
      "llm_response_path": "reply.text"
    }
  }
}
```

### 请求体构建 (`_build_payload`)

先从 `session_payload["messages"]` 把多轮对话拼成 `messages_text`：

```
system:
You are a coding assistant...

system:
Available tools: 1. list_files...

system:
Current working directory: /path

user:
项目结构是什么？

assistant:
{"type":"tool_call","tool":"list_files","arguments":{"path":"."}}
```

占位符替换：
- `{messages_text}` → 上面的纯文本
- `{messages_json}` → messages 的 JSON 数组形式

替换后发出：
```json
{"input": "system:\nYou are a coding assistant...\n\nuser:\n项目结构是什么？\n\nassistant:\n{\"type\":\"tool_call\",...}"}
```

### 响应提取 (`_extract_response`)

假设 API 返回：
```json
{
  "messageId": "m1",
  "time": "2026-04-26T12:00:00Z",
  "content": "{\"type\":\"final\",\"content\":\"src/ 和 tests/\"}"
}
```

`response_path = "content"` → 取到 `{"type":"final","content":"src/ 和 tests/"}` → 返回给 `protocol.parse_model_output()` 解析。

**其他字段（messageId、time）全部丢弃。**

## 如果公司 API 是有状态的

针对用户提到的实际 API 格式：AI 回答以 `{"messageId":"m1","time":"...","content":"..."}` 返回，且**必须回传 messageId 才能保持同一会话**。

这意味着 API 请求体可能是交替数组：
```json
[
  {"role": "user", "content": "项目结构是什么？"},
  {"messageId": "m1", "content": "src/ 和 tests/"},
  {"role": "user", "content": "具体说说 src/"}
]
```

当前 `GenericJSONClient` **无法适配**，因为：

1. `body_template` 只能生成单一 JSON 对象，不是交替数组
2. `_extract_response` 只取一个字段，`messageId` 丢弃了，下轮请求没法回传

### 改动点 1：持久化 API 往返记录

在 `session_payload` 中新增字段 `api_conversation`，每次 API 调用后把完整响应条目追加上去：

```python
# GenericJSONClient.chat() 中
def chat(self, messages, session_payload):
    api_conversation = session_payload.setdefault("api_conversation", [])
    payload = self._build_payload(messages, api_conversation)
    body = _http_post(self.base_url, json.dumps(payload), headers, self.timeout)
    data = json.loads(body)
    # 保存 AI 回答（含 messageId），下轮请求要回传
    api_conversation.append({"messageId": data["messageId"], "content": data["content"]})
    return data["content"]
```

### 改动点 2：请求体构建

`_build_payload` 不再用 `body_template`，而是把前面保存的 `api_conversation` 和当前用户消息拼成交替数组：

```python
def _build_payload(self, messages, api_conversation):
    entries = list(api_conversation)
    # 从 messages 里找到最后一条 user 消息
    user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    entries.append({"role": "user", "content": user_msg})
    return entries
```

### 改动点 3：agent.py 透传 session_payload

当前 `client.chat(messages)` 没有 session_payload。需要改成 `client.chat(messages, session_payload)`：

```python
# agent.py handle_user_message 中
raw_output = client.chat(messages, session_payload)

# GenericJSONClient
def chat(self, messages, session_payload=None):
    ...
```

`OpenAICompatibleClient` 不受影响，保持原有签名即可。

### 改动点 4：session.py 新增持久化字段

`create_session` 需要初始化 `api_conversation`：

```python
def create_session(workdir):
    payload = {
        ...
        "api_conversation": [],    # ← 新增
    }
```

## 快速定位文件

| 文件 | 职责 | 是否需要改 |
|---|---|---|
| `app/llm_client.py` | HTTP 请求/响应处理 | **是** — 核心改造点 |
| `app/agent.py` | 工具调用循环 | 可能 — 需透传 session_payload |
| `app/session.py` | 会话持久化 | 可能 — 需新增 api_conversation 字段 |
| `app/config.py` | 配置解析 | 不改 |
| `app/protocol.py` | JSON 协议解析 | 不改 |
| `app/messages.py` | 历史消息组装 | 不改 |
| `app/tools.py` | 工具派发 | 不改 |
| `app/prompts.py` | 系统提示词 | 不改 |
| `app/cli.py` | CLI 命令 | 不改 |

## 关键数据流总结

```
用户输入 "项目结构？"
  → session_payload["messages"] 追加 {"role": "user", "content": "项目结构？"}
  → messages.py 组装 system + history 为 messages 列表
  → agent._call_model_with_retry(client, messages, settings)
    → client.chat(messages)  # 发 HTTP
      → 对于 generic_json: body_template + {messages_text} → POST → 解析 response_path
    → raw_output = "{\"type\":\"tool_call\",\"tool\":\"list_files\",\"arguments\":{\"path\":\".\"}}"
    → protocol.parse_model_output(raw_output) → {"type":"tool_call", "tool":"list_files", ...}
  → tools.run_tool("list_files", {"path": "."}, settings)
  → 结果注入 messages（role: system）
  → 重复直到 final 或 budget 耗尽
  → 从 parsed 提取 content → 打印给用户 → 存 session
```
