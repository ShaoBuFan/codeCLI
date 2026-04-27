# Code Agent CLI 重构规格：外部状态机 + 工具集最小化

> 本文档面向 Claude Code。阅读完整后再开始任何文件修改。

---

## 核心问题诊断

当前 Code Agent 的典型反模式：

```
[用户输入] → [单一大 Prompt（角色+状态+工具+历史）] → [模型输出] → 重复
```

问题：
- 模型需要从 context 里"记住"当前进度（上下文越长越不可靠）
- 所有工具始终暴露（模型选错工具的概率随工具数线性增长）
- 没有明确的阶段边界，失败时不知道从哪里重试

目标架构：

```
[用户输入] → [Orchestrator 读取外部 State] → [按当前 Phase 注入最小工具集] → [模型输出单步] → [Orchestrator 更新 State] → 循环
```

---

## 一、外部状态机

### 1.1 设计原则

**核心思想**：模型不负责记忆，CLI 负责记忆。每次调用模型时，把当前状态作为输入注入 prompt，而不是让模型从对话历史里推断。

### 1.2 State 数据结构

```typescript
// types/agent-state.ts

export type Phase =
  | 'IDLE'
  | 'EXPLORING'    // 读取文件结构、理解代码库
  | 'PLANNING'     // 生成执行计划（步骤列表）
  | 'PATCHING'     // 写文件、执行修改
  | 'VERIFYING'    // 运行测试、检查结果
  | 'DONE'
  | 'FAILED';

export interface AgentStep {
  id: string;                  // uuid
  intent: string;              // 人类可读意图，如 "修改 src/auth.ts 中的 token 校验逻辑"
  target_files: string[];      // 涉及的文件路径
  status: 'pending' | 'running' | 'done' | 'failed';
  result?: string;             // 执行结果摘要（不是全文，只记关键事实）
  error?: string;              // 失败原因（结构化）
  retry_count: number;
}

export interface AgentState {
  task_id: string;
  task_description: string;    // 原始用户输入，不变
  phase: Phase;

  // 执行进度
  steps: AgentStep[];
  current_step_index: number;

  // 关键事实层（跨步骤必须保留的信息，模型不能靠 context 记）
  facts: {
    files_read: string[];          // 已读取过的文件
    files_modified: string[];      // 已修改的文件
    key_findings: string[];        // 探索阶段发现的重要信息，每条 < 100 字
    constraints: string[];         // 执行约束，如 "不能修改 API 接口签名"
  };

  // 快照与回滚
  snapshot_id?: string;            // 修改前的代码快照标识（git stash hash 或临时目录）
  
  // 元信息
  created_at: string;
  updated_at: string;
  iteration_count: number;         // 防止无限循环
  max_iterations: number;          // 默认 20
}
```

### 1.3 State 持久化

```typescript
// state/state-manager.ts

import fs from 'fs';
import path from 'path';

const STATE_DIR = '.agent-state';

export class StateManager {
  private statePath: string;

  constructor(taskId: string) {
    this.statePath = path.join(STATE_DIR, `${taskId}.json`);
    fs.mkdirSync(STATE_DIR, { recursive: true });
  }

  load(): AgentState | null {
    if (!fs.existsSync(this.statePath)) return null;
    return JSON.parse(fs.readFileSync(this.statePath, 'utf-8'));
  }

  save(state: AgentState): void {
    state.updated_at = new Date().toISOString();
    fs.writeFileSync(this.statePath, JSON.stringify(state, null, 2));
  }

  // 用于构建注入 prompt 的状态摘要（不是全部字段）
  toPromptContext(state: AgentState): string {
    const currentStep = state.steps[state.current_step_index];
    return `
## 当前任务状态
- 任务: ${state.task_description}
- 阶段: ${state.phase}
- 当前步骤 (${state.current_step_index + 1}/${state.steps.length}): ${currentStep?.intent ?? '无'}
- 已修改文件: ${state.facts.files_modified.join(', ') || '无'}
- 关键发现:
${state.facts.key_findings.map(f => `  - ${f}`).join('\n') || '  无'}
- 约束:
${state.facts.constraints.map(c => `  - ${c}`).join('\n') || '  无'}
    `.trim();
  }
}
```

### 1.4 Phase 转移逻辑

```typescript
// state/phase-machine.ts

export type PhaseTransition = {
  from: Phase;
  to: Phase;
  condition: (state: AgentState) => boolean;
};

export const TRANSITIONS: PhaseTransition[] = [
  {
    from: 'IDLE',
    to: 'EXPLORING',
    condition: () => true,
  },
  {
    from: 'EXPLORING',
    to: 'PLANNING',
    // 所有目标文件都已读取
    condition: (s) => s.facts.files_read.length > 0,
  },
  {
    from: 'PLANNING',
    to: 'PATCHING',
    // 步骤列表已生成
    condition: (s) => s.steps.length > 0,
  },
  {
    from: 'PATCHING',
    to: 'VERIFYING',
    // 所有步骤执行完毕
    condition: (s) => s.steps.every(step => step.status === 'done'),
  },
  {
    from: 'VERIFYING',
    to: 'DONE',
    condition: (s) => {
      const verifyStep = s.steps.find(step => step.intent.includes('verify'));
      return verifyStep?.status === 'done';
    },
  },
  // 任何阶段失败超过阈值都进入 FAILED
  {
    from: 'PATCHING',
    to: 'FAILED',
    condition: (s) => {
      const current = s.steps[s.current_step_index];
      return (current?.retry_count ?? 0) >= 3;
    },
  },
];

export function getNextPhase(state: AgentState): Phase {
  const validTransition = TRANSITIONS.find(
    t => t.from === state.phase && t.condition(state)
  );
  return validTransition?.to ?? state.phase;
}
```

---

## 二、工具集最小化

### 2.1 设计原则

**每个 Phase 只暴露该阶段语义上合法的工具**。不是出于安全考虑，而是减少模型的决策空间——工具越少，选择越准确。

### 2.2 工具定义

```typescript
// tools/tool-registry.ts

export type ToolName =
  | 'read_file'
  | 'list_directory'
  | 'search_code'       // grep/ast 搜索
  | 'write_file'
  | 'apply_diff'
  | 'run_command'       // 执行 shell 命令（受限）
  | 'run_tests'
  | 'report_plan'       // 仅 PLANNING 阶段：输出步骤列表
  | 'report_findings'   // 仅 EXPLORING 阶段：输出关键发现
  | 'report_done'       // 标记任务完成
  | 'report_blocked';   // 标记卡住，触发 Planner 重新规划

// 每个 Phase 允许的工具集
export const PHASE_TOOLS: Record<Phase, ToolName[]> = {
  IDLE: [],
  EXPLORING: [
    'read_file',
    'list_directory',
    'search_code',
    'report_findings',   // 探索完成后用这个输出发现，不用 write_file
  ],
  PLANNING: [
    'read_file',         // 允许补充读取，但不能写
    'report_plan',       // 唯一的输出通道：生成结构化步骤列表
  ],
  PATCHING: [
    'read_file',         // 修改前可以再读一次确认
    'write_file',
    'apply_diff',
    'run_command',       // 受限：只允许 lint / format，不允许任意 shell
    'report_blocked',    // 遇到无法解决的障碍时调用
  ],
  VERIFYING: [
    'read_file',
    'run_tests',
    'report_done',
    'report_blocked',
  ],
  DONE: [],
  FAILED: [],
};
```

### 2.3 工具实现（关键几个）

```typescript
// tools/implementations.ts

export const TOOL_IMPLEMENTATIONS = {

  // report_findings: EXPLORING 阶段的唯一输出通道
  // 强制结构化，不允许自由文本
  report_findings: {
    description: '报告探索阶段的发现。在开始 PLANNING 之前必须调用此工具。',
    parameters: {
      type: 'object',
      properties: {
        key_findings: {
          type: 'array',
          items: { type: 'string', maxLength: 100 },
          description: '关键发现列表，每条不超过100字',
          maxItems: 10,
        },
        relevant_files: {
          type: 'array',
          items: { type: 'string' },
          description: '与任务直接相关的文件路径列表',
        },
        constraints: {
          type: 'array',
          items: { type: 'string' },
          description: '发现的约束条件（如接口不能改、兼容性要求等）',
        },
      },
      required: ['key_findings', 'relevant_files'],
    },
    // Orchestrator 收到后直接写入 state.facts，触发 Phase 转移
    handler: async (args: any, state: AgentState) => {
      state.facts.key_findings = args.key_findings;
      state.facts.files_read = args.relevant_files;
      state.facts.constraints = args.constraints ?? [];
      return { success: true };
    },
  },

  // report_plan: PLANNING 阶段的唯一输出通道
  report_plan: {
    description: '提交执行计划。计划必须是文件级别的步骤列表。',
    parameters: {
      type: 'object',
      properties: {
        steps: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              intent: { type: 'string', description: '这一步要做什么（人类可读）' },
              target_files: { type: 'array', items: { type: 'string' } },
            },
            required: ['intent', 'target_files'],
          },
          minItems: 1,
          maxItems: 10,
        },
      },
      required: ['steps'],
    },
    handler: async (args: any, state: AgentState) => {
      state.steps = args.steps.map((s: any, i: number) => ({
        id: `step-${i}`,
        intent: s.intent,
        target_files: s.target_files,
        status: 'pending',
        retry_count: 0,
      }));
      state.current_step_index = 0;
      return { success: true };
    },
  },

  // report_blocked: 结构化的失败上报（不是自由文本）
  report_blocked: {
    description: '当前步骤无法继续时调用。必须说明具体原因。',
    parameters: {
      type: 'object',
      properties: {
        reason_type: {
          type: 'string',
          enum: [
            'file_not_found',
            'dependency_conflict',
            'test_failure',
            'ambiguous_requirement',
            'permission_denied',
            'other',
          ],
        },
        detail: { type: 'string', description: '具体错误信息或位置（行号、文件名等）' },
        suggested_action: {
          type: 'string',
          enum: ['retry', 'skip', 'replan', 'abort'],
        },
      },
      required: ['reason_type', 'detail', 'suggested_action'],
    },
    handler: async (args: any, state: AgentState) => {
      const current = state.steps[state.current_step_index];
      if (current) {
        current.status = 'failed';
        current.error = `[${args.reason_type}] ${args.detail}`;
      }
      // Orchestrator 根据 suggested_action 决定下一步
      return { blocked: true, ...args };
    },
  },

  // run_command: 受限的命令执行（白名单）
  run_command: {
    description: '执行允许的命令（仅限 lint/format 类）。',
    parameters: {
      type: 'object',
      properties: {
        command: {
          type: 'string',
          enum: [
            'npm run lint',
            'npm run format',
            'npx eslint --fix',
            'npx prettier --write',
          ],
        },
        target: { type: 'string', description: '目标文件或目录' },
      },
      required: ['command'],
    },
    handler: async (args: any) => {
      // 只允许白名单内的命令，拒绝任意 shell
      const { execSync } = await import('child_process');
      const output = execSync(`${args.command} ${args.target ?? ''}`, {
        encoding: 'utf-8',
        timeout: 30000,
      });
      return { output };
    },
  },
};
```

---

## 三、Orchestrator 主循环

```typescript
// orchestrator.ts

export class AgentOrchestrator {
  private stateManager: StateManager;
  private llmClient: LLMClient;

  async run(taskDescription: string): Promise<void> {
    const taskId = generateTaskId();
    const state = this.initState(taskId, taskDescription);

    // 执行前快照（git stash 或复制目录）
    state.snapshot_id = await this.createSnapshot();
    this.stateManager.save(state);

    while (!['DONE', 'FAILED'].includes(state.phase)) {
      if (state.iteration_count >= state.max_iterations) {
        state.phase = 'FAILED';
        console.error('达到最大迭代次数，任务中止');
        break;
      }

      await this.executePhaseStep(state);
      state.iteration_count++;
      this.stateManager.save(state);

      // 尝试 Phase 转移
      const nextPhase = getNextPhase(state);
      if (nextPhase !== state.phase) {
        console.log(`Phase 转移: ${state.phase} → ${nextPhase}`);
        state.phase = nextPhase;
        this.stateManager.save(state);
      }
    }

    if (state.phase === 'FAILED') {
      await this.rollback(state.snapshot_id);
    }
  }

  private async executePhaseStep(state: AgentState): Promise<void> {
    // 1. 获取当前 Phase 的工具集（最小化）
    const availableTools = PHASE_TOOLS[state.phase].map(
      name => TOOL_IMPLEMENTATIONS[name]
    );

    // 2. 构建 prompt（注入状态，不是靠历史 context）
    const prompt = this.buildPrompt(state, availableTools);

    // 3. 调用模型（只有当前步骤的相关文件内容，不是整个历史）
    const response = await this.llmClient.complete({
      system: this.getSystemPrompt(state.phase),   // 按 Phase 切换系统提示
      user: prompt,
      tools: availableTools,
    });

    // 4. 解析工具调用，执行，更新 state
    await this.handleToolCall(response, state);
  }

  private buildPrompt(state: AgentState, tools: any[]): string {
    // 状态摘要注入（不是全部 context）
    const stateContext = this.stateManager.toPromptContext(state);

    // 当前步骤的相关文件内容（AST 裁剪后）
    const currentStep = state.steps[state.current_step_index];
    const fileContext = currentStep
      ? this.loadRelevantFileContext(currentStep.target_files)
      : '';

    return `
${stateContext}

${fileContext ? `## 相关文件内容\n${fileContext}` : ''}

## 你的任务
${this.getPhaseInstruction(state)}

## 可用工具
${tools.map(t => `- ${t.name}: ${t.description}`).join('\n')}

请调用一个工具，且只调用一个工具。
    `.trim();
  }

  // 按 Phase 给出不同的单步指令
  private getPhaseInstruction(state: AgentState): string {
    const instructions: Record<Phase, string> = {
      IDLE: '',
      EXPLORING: '读取相关文件，理解代码结构。完成后调用 report_findings。',
      PLANNING: '根据发现制定执行计划。调用 report_plan 提交步骤列表（文件级别，不超过10步）。',
      PATCHING: `执行步骤 ${state.current_step_index + 1}: ${state.steps[state.current_step_index]?.intent ?? ''}。修改完成后该步骤自动标记为 done。遇到障碍调用 report_blocked。`,
      VERIFYING: '运行测试，确认修改符合预期。通过后调用 report_done，失败调用 report_blocked。',
      DONE: '',
      FAILED: '',
    };
    return instructions[state.phase];
  }

  // 步骤完成后自动推进 current_step_index
  private async handleToolCall(response: any, state: AgentState): Promise<void> {
    const toolCall = response.tool_call;
    if (!toolCall) return;

    // 校验工具是否在当前 Phase 的允许列表内
    if (!PHASE_TOOLS[state.phase].includes(toolCall.name)) {
      console.warn(`工具 ${toolCall.name} 在当前 Phase ${state.phase} 中不可用，跳过`);
      return;
    }

    const tool = TOOL_IMPLEMENTATIONS[toolCall.name];
    const result = await tool.handler(toolCall.args, state);

    // PATCHING 阶段：write_file 或 apply_diff 成功后自动推进步骤
    if (['write_file', 'apply_diff'].includes(toolCall.name) && result.success) {
      const current = state.steps[state.current_step_index];
      if (current) {
        current.status = 'done';
        current.result = `修改已写入: ${toolCall.args.path}`;
        state.facts.files_modified.push(toolCall.args.path);
      }
      // 推进到下一步
      if (state.current_step_index < state.steps.length - 1) {
        state.current_step_index++;
      }
    }
  }

  // 文件内容裁剪：只返回与当前步骤相关的函数/类，不是整文件
  private loadRelevantFileContext(filePaths: string[]): string {
    // 实现：读取文件 → AST 解析 → 只提取相关节点
    // 超出 token 预算时截断并添加 "...（省略 N 行）" 标记
    // 这里是伪代码，实际实现根据语言选择 AST 工具
    return filePaths.map(p => {
      const content = fs.readFileSync(p, 'utf-8');
      if (content.length > 4000) {
        return `// ${p} （已裁剪，仅显示前4000字符）\n${content.slice(0, 4000)}\n// ... 省略`;
      }
      return `// ${p}\n${content}`;
    }).join('\n\n');
  }
}
```

---

## 四、文件结构

```
src/
├── types/
│   └── agent-state.ts          # AgentState, Phase, AgentStep 类型定义
├── state/
│   ├── state-manager.ts        # 持久化 + toPromptContext()
│   └── phase-machine.ts        # TRANSITIONS + getNextPhase()
├── tools/
│   ├── tool-registry.ts        # PHASE_TOOLS 映射表
│   └── implementations.ts      # 每个工具的 handler
├── orchestrator.ts             # 主循环
├── llm-client.ts               # 模型调用封装（带重试、格式校验）
└── index.ts                    # CLI 入口
.agent-state/
└── {task_id}.json              # 运行时状态（加入 .gitignore）
```

---

## 五、重构实施顺序（给 Claude Code 的执行步骤）

按以下顺序重构，每步完成后运行测试再继续：

1. **创建 `src/types/agent-state.ts`** — 先定死类型，后续所有模块都依赖它。
2. **创建 `src/state/state-manager.ts`** — 实现 load/save/toPromptContext。
3. **创建 `src/state/phase-machine.ts`** — 实现 TRANSITIONS 和 getNextPhase。
4. **创建 `src/tools/tool-registry.ts`** — 实现 PHASE_TOOLS 映射，暂时先用空 handler 占位。
5. **实现 `src/tools/implementations.ts`** — 逐个实现 handler，优先级：report_findings > report_plan > write_file > report_blocked > 其余。
6. **重构现有 Orchestrator** — 将原有的单 prompt 循环替换为状态驱动的主循环，保留原有 LLM 调用接口不动。
7. **在 CLI 入口 `src/index.ts` 接入新 Orchestrator** — 可以加 `--legacy` flag 切回旧版本，保留回退路径。
8. **端到端测试** — 用一个简单的文件修改任务（如"给某函数加注释"）跑完整 Phase 流程，确认状态文件正确更新。

---

## 六、验证检查清单

重构完成后，确认以下行为：

- [ ] 状态文件 `.agent-state/{task_id}.json` 在每轮调用后正确更新
- [ ] EXPLORING 阶段的 prompt 中不包含 write_file、apply_diff 工具
- [ ] PATCHING 阶段的 prompt 中不包含 report_plan 工具
- [ ] 模型输出的工具名不在 PHASE_TOOLS 白名单内时，Orchestrator 拒绝执行而不是崩溃
- [ ] write_file 成功后 state.current_step_index 自动 +1
- [ ] iteration_count 达到 max_iterations 时任务中止并触发回滚
- [ ] `.agent-state/` 目录已加入 `.gitignore`
