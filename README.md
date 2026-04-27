# codeCLI

Python 标准库，零依赖。自定义 JSON 文本协议驱动 LLM，支持 DeepSeek 和 Mattermost 两种后端。

## 快速开始

```bash
python app/main.py config --init-template        # 生成配置模板
python app/main.py config --provider deepseek --api-key "sk-..."
python app/main.py chat
```

## 两种运行模式

```bash
python app/main.py chat                           # legacy：动态步数预算，模型自主探索
python app/main.py chat --mode stateful           # 状态机：phase 驱动，按阶段最小工具集
```

stateful 模式下，模型按 IDLE → EXPLORING → PLANNING → PATCHING → VERIFYING → DONE 六阶段推进，每阶段只暴露语义相关的工具。进度、发现、约束全部外部存储，不靠对话历史记忆。

## 架构

```
main.py → cli.py
            ├── agent.py          旧循环 (legacy)
            ├── orchestrator.py   新状态机 (stateful)
            │     ├── state.py         AgentState 类型 + StateManager
            │     └── phase.py         阶段转移 + 工具白名单
            ├── llm_client.py     OpenAICompatibleClient / MattermostClient
            ├── protocol.py       JSON 协议解析
            ├── messages.py       消息组装 + PROJECT.md 注入
            ├── tools.py          工具派发 (含 report_* 结构化报告工具)
            │     ├── files.py        文件操作 + 编码回退
            │     └── safety.py       路径沙箱 + 用户确认
            ├── prompts.py        系统提示词
            ├── session.py        会话持久化
            └── init.py           项目初始化 (评分 → 探索 → PROJECT.md)
```

16 模块，2156 行，单向依赖无循环。

## 协议

```json
{"type": "final",     "content": "回答文本"}
{"type": "tool_call", "tool": "read_file", "arguments": {"path": "app/main.py"}}
```

工具调用循环：用户消息 → LLM 返回 tool_call → 执行 → 结果注入 → 再问 LLM → ... → final → 输出。起始 10 步，最多 25 步。格式不符自动 retry（最多 2 次，指明具体错误）。

## 工具

| 工具 | 说明 |
|---|---|
| `list_files` | 列出目录文件（限 200） |
| `read_file` | 读取文件（限 200KB） |
| `search_text` | 搜索文件内容（限 100 结果） |
| `write_file` | 写文件（需确认） |

stateful 模式额外工具：

| 工具 | 阶段 | 说明 |
|---|---|---|
| `report_findings` | EXPLORING | 结构化报告关键发现 |
| `report_plan` | PLANNING | 生成文件级步骤列表 |
| `report_blocked` | PATCHING/VERIFYING | 结构化失败上报 |
| `report_done` | VERIFYING | 标记完成，触发 DONE |

## 配置

```json
{
  "active_provider": "deepseek",
  "llm_debug": false,
  "providers": {
    "deepseek": {
      "llm_provider": "openai_compatible",
      "llm_api_key": "",
      "llm_base_url": "https://api.deepseek.com/chat/completions",
      "llm_model": "deepseek-v4-flash"
    },
    "mattermost": {
      "llm_provider": "mattermost",
      "llm_base_url": "https://mattermost.aslead.cloud/plugins/aslead-chatgpt",
      "llm_model_key": "sendMessageToChatGPT",
      "access_team": "",
      "mmauth_token": "",
      "mmuser_id": "",
      "csrf_token": ""
    }
  }
}
```

环境变量：`LLM_API_KEY` `LLM_BASE_URL` `LLM_MODEL` `LLM_PROVIDER` `LLM_TIMEOUT`

## CLI

```bash
python app/main.py ask "问题"                # 单次提问
python app/main.py chat                      # 交互 REPL
python app/main.py chat --session <id>       # 恢复会话
python app/main.py chat --mode stateful      # 状态机模式
python app/main.py sessions                  # 会话列表
python app/main.py config --show             # 当前配置
python app/main.py config --init-template    # 生成模板
```

chat 命令：

```
/help        /provider <name>    /load <id>
/cwd         /session            /files [path]       /clear
/config      /sessions           /read <path>        /exit
/init                             /search <kw> [path]
```

`/init` — 项目初始化：扫描全部文件 → 评分排序（大小 + 关键名加权 + 噪声降权）→ 读最优候选至 300KB → 交模型生成 `PROJECT.md`。生成的 PROJECT.md 后续对话自动注入，切换 session 后重新加载。
