# codeCLI

一个只依赖 Python 标准库的本地控制台 AI 助手骨架。

## 当前能力

- `python app/main.py ask "你的问题"`
- `python app/main.py chat`
- `python app/main.py config --api-key "你的key"`
- 本地 session 持久化
- 读取文件、搜索文本、列出文件
- 可确认后执行 Git Bash 命令
- 可确认后写文件
- 支持 OpenAI 兼容接口和自定义 JSON HTTP 接口

## 环境变量

优先使用项目内配置文件：

- `data/local_config.json`

也兼容环境变量：

- `LLM_PROVIDER`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `LLM_HEADERS_JSON`
- `LLM_BODY_TEMPLATE_JSON`
- `LLM_RESPONSE_PATH`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `DEEPSEEK_TIMEOUT`
- `GIT_BASH_PATH`

默认值：

- `LLM_PROVIDER=openai_compatible`
- `LLM_BASE_URL=https://api.deepseek.com/chat/completions`
- `LLM_MODEL=deepseek-v4-flash`

## 用法

```bash
python app/main.py config --provider openai_compatible --api-key "你的key"
python app/main.py ask "帮我分析当前项目结构"
python app/main.py chat
python app/main.py sessions
```

更方便的统一配置方式：

```bash
python app/main.py config --init-template
python app/main.py config --show
```

然后直接编辑项目内的 `data/local_config.json`。

自定义 JSON HTTP 接口示例：

```bash
python app/main.py config --provider generic_json
python app/main.py config --base-url "https://your-company-endpoint"
python app/main.py config --headers-json "{\"Cookie\":\"...\",\"X-Token\":\"...\"}"
python app/main.py config --body-template-json "{\"input\":\"{messages_text}\"}"
python app/main.py config --response-path "reply.text"
python app/main.py config --debug on
```

推荐做法不是一行一行执行，而是一次生成模板后直接改 `data/local_config.json`，例如：

```json
{
  "active_provider": "company",
  "llm_debug": true,
  "providers": {
    "deepseek": {
      "llm_provider": "openai_compatible",
      "llm_api_key": "你的deepseek key",
      "llm_base_url": "https://api.deepseek.com/chat/completions",
      "llm_model": "deepseek-v4-flash"
    },
    "company": {
      "llm_provider": "generic_json",
      "llm_base_url": "https://your-company-endpoint",
      "llm_headers": {
        "Cookie": "你的cookie",
        "Authorization": "Bearer 你的token",
        "X-CSRF-Token": "你的token"
      },
      "llm_body_template": {
        "input": "{messages_text}",
        "conversation_id": "abc"
      },
      "llm_response_path": "reply.text"
    }
  },
  "git_bash_path": "C:\\Program Files\\Git\\bin\\bash.exe"
}
```

仍然兼容旧格式：

- `llm_headers_json`
- `llm_body_template_json`

但更推荐直接使用对象格式：

- `llm_headers`
- `llm_body_template`

## chat 内置命令

在 `chat` 模式下支持：

```text
/help
/provider
/provider <name>
/session
/sessions
/files [path]
/read <path>
/search <keyword> [path]
/config
/clear
/exit
```

## 说明

- 只使用 Python 标准库。
- 本地配置默认只写入当前项目下的 `data/` 目录。
- 模型工具调用通过 JSON 文本协议实现，不依赖 SDK 的 function calling。
- shell 执行默认走 Git Bash：`bash -lc <command>`。
- 调试模式会把请求和响应摘要写到 `data/logs/llm_debug.jsonl`，并对常见敏感请求头做掩码。
