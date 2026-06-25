# Harness Gap Analysis

这份文档把外部 Agent/harness 设计批评拆成工程能力项，并对照 `minimal-cli-agent` 当前设计做分类。目的不是照单全收，而是把值得吸收的部分变成明确路线图。

## 分类说明

- 已规划未实现：当前 architecture/README 已经有边界或路线图，但还没有生产级实现。
- 未规划未实现：当前文档没有明确覆盖，应该补进路线图。
- 已由当前设计规避：当前设计已经避免了同类问题，或具备更清晰边界。
- 优于当前设计：批评里提出的要求比我们现状更成熟，应该吸收。

## 已规划未实现

| 主题 | 当前状态 | 下一步 |
| --- | --- | --- |
| 工具执行管道 | 已有 `ToolExecutionPipeline` 阶段形状，但 `ResolveDecision` 仍是 pass-through，`AutoVerify` 很薄 | 补参数 schema、决策仲裁、确认 UI 适配、重试和格式化策略 |
| 权限 hard gate | 已有 `ShellPermissionPolicy`、`ToolDecision`、`plan` 跳过执行、`yolo` 仍受硬拒绝规则限制 | 增加命令 allow/deny 规则、工作区写入边界、网络访问策略、审计日志 |
| 上下文压缩 | 当前是本地裁剪加提示，不是语义压缩 | 增加模型总结、原始 transcript 保留、可召回 summary/memory |
| Memory 管理 | README/architecture 已规划 transcript/working/project/task memory | 实现 EventStore 或 SQLite session log，再做 memory retrieval |
| SubAgent / GroupSession | 已作为 roadmap 预留 | 先实现 `SubAgentRunner` 和隔离 session，再做 group event log |
| Workflow 委托 | 已规划 `plan/delegate/wait/merge/verify` 原语 | 需要 typed workflow state，而不是 prompt 内隐式计划 |
| 并发和文件锁 | 已明确暂不实现 | 等多工具调用出现后再做读写分桶、同文件写锁、取消和超时传播 |
| MCP / plugin / skill | 已规划挂在 `ToolRegistry` 后面 | 先定义 tool manifest，再接 MCP discovery |

## 未规划未实现

| 主题 | 风险 | 建议 |
| --- | --- | --- |
| 完整 JSON Schema | 当前已有最小 validator，但还不是 JSON Schema | 引入 typed `ToolCall` 参数 schema，支持字段级错误和正确格式示例 |
| 工具模糊识别 | 当前已有显式 alias，但没有 fuzzy suggestion | 在 Discovery 阶段增加安全的 fuzzy suggestion，但不自动执行高风险猜测 |
| 文件读取工具性能 | 已实现 `read_tail` / `read_forward` 基线，但还没有更细的编码、二进制和分页状态治理 | 继续补 byte/line 双模式、二进制检测和分页游标 |
| grep/search top-k | 已实现 `search(pattern,path,top_k,max_files)` 基线，但还没有 timeout 和 ignore 文件规则 | 继续补 timeout、ignore rules、渐进输出和 richer ranking |
| 结构化文本编辑校验 | JSON/XML/YAML 被模型写坏后，后端如果无校验会沉默失败 | 写入后按文件类型 parse/format/validate；失败时阻断并把错误返回给模型 |
| Plan Mode 上下文隔离 | 探索噪音会污染执行阶段，计划和执行工具集也可能混在一起 | Plan 应该是独立 context、独立 tool allowlist，并输出 typed plan artifact |
| OS shell adapter | 只假设 bash 会伤害 Windows/Powershell/cmd/Git Bash 场景 | 增加 `ShellAdapter`：bash/zsh/powershell/cmd/git-bash；显式暴露 shell、encoding、path rules 给模型 |
| 环境变量刷新 | 长会话里环境变化不能被 runtime 感知 | Environment 每次执行前重建 env snapshot，并记录差异或允许 hook 更新 |
| 编码和换行 | Windows/codepage/二进制输出可能破坏 observation | 统一 stdout/stderr decoding 策略，保留 raw bytes 截断摘要 |
| 提示词预算治理 | 工具说明堆进 system prompt 会挤占上下文并制造 lost-in-the-middle | Prompt 应该按角色和可用工具动态生成，工具文档短描述默认注入，长说明按需召回 |
| AGENTS/项目规则注入治理 | 项目规则可能臃肿、冲突或被提示注入污染 | 项目规则需要分层、去重、冲突检测、来源标注和预算上限 |

## 已由当前设计规避

| 问题 | 当前设计优势 |
| --- | --- |
| Agent 持有 session 导致多会话混乱 | `Agent.chat_stream(message, context)` 是无状态入口，`ChatContext` 由调用方传入 |
| 工具调用散落在 Agent loop | `Agent` 只解析 action，执行经过 `AgentHarness`、`ToolRegistry`、`ToolExecutionPipeline` |
| 文件工具整文件读取风险 | 已有 `read_tail`、`read_forward` 和 `search` 的有界基线，避免所有读取都走 `read_file` |
| Plan Mode 只靠模型自觉不执行 | 当前 `plan` 权限模式在 policy/pipeline 层返回 `skip`，shell 不会执行 |
| 权限模式只是提示词约定 | `ToolDecision` 是代码层决策，`deny/skip` 是 hard gate |
| Codex 登录态误走 Platform API | `codex` profile 检测 Codex auth 后走 Codex CLI adapter，不把 `tokens.access_token` 发到 `api.openai.com` |
| 提示词过早膨胀 | 当前只有极小 system prompt，并只注入 `bash-action` / `tool-action` 的短格式示例，没有把大量工具教程塞入 prompt |
| 边界不透明 | README/architecture 已拆出 implemented/reserved/not implemented |

## 优于当前设计、应吸收

| 能力 | 为什么优先 |
| --- | --- |
| 完整 schema validation + repair observation | 当前已有最小 repair observation，下一步要支持 JSON Schema 和字段级错误 |
| Plan/Execute 双上下文 | Coding agent 的计划噪音和执行历史应该隔离，否则长任务会持续污染上下文 |
| 文件工具流式读取和搜索 top-k | 已有基线，下一步应补 timeout、ignore rules、分页游标和更强 ranking |
| 结构化编辑校验 | 能直接减少模型幻觉导致的坏 JSON/XML/YAML/配置文件 |
| ShellAdapter 跨平台设计 | CLI agent 不能长期只假设 bash，尤其不能把 Git Bash 当成 Windows 原生 shell |
| Prompt budget policy | harness 不只管工具和权限，也要管提示词预算、角色差异和规则注入 |
| EventStore + memory retrieval | 压缩后无法召回会让长期任务丢状态；需要原始日志和可检索 memory 双轨 |

## 新方向

### Phase 1: Tool Harness Hardening

目标是让工具错误可恢复、可审计、可验证。

- 为 `ToolSpec` 增加 JSON Schema、风险等级、输出 schema。
- `Validation` 阶段已经返回可恢复 observation，下一步补字段级错误和 JSON 格式示例。
- `Formatting` 阶段统一 observation 格式，避免模型收到散乱异常文本。
- 为 `ToolExecutionPipeline` 增加 hook 仲裁和测试覆盖。

### Phase 2: File Tool Baseline

目标是先做少量高质量内置工具，而不是堆工具数量。

- `read_tail(path, lines, max_bytes)` 已使用尾部窗口读取，不整文件读入。
- `read_forward(path, offset, limit)` 已支持 byte offset 分页和最大输出。
- `search(pattern, path, top_k, max_files)` 已有 top-k 和文件数边界；下一步补 timeout 和 ignore rules。
- `write/edit` 对 JSON/YAML/XML/TOML 做 parse validation。

### Phase 3: Plan/Execute Separation

目标是让 Plan Mode 变成真正的 harness 能力。

- `plan` 阶段只能用只读工具和 `WritePlan`。
- `execute` 阶段读取 typed plan artifact，并使用不同 tool allowlist。
- plan transcript 不直接并入 execute context，只保留计划摘要、决策和必要证据。

### Phase 4: Cross-platform Environment

目标是让 shell 执行不再假设 bash。

- 增加 `ShellAdapter` 接口。
- 支持 bash/zsh/powershell/cmd/git-bash。
- observation 中明确 shell kind、cwd、encoding、path separator。
- 每次执行前刷新 env snapshot，并做输出编码处理。

### Phase 5: Prompt and Memory Governance

目标是把 prompt 也纳入 harness，而不是无限堆 system text。

- 按 agent role 生成短 system prompt。
- 工具说明短描述默认注入，长说明按需召回。
- AGENTS/项目规则分层、去重、冲突检测、预算上限。
- EventStore 保存完整 transcript，WorkingMemory 只保存压缩上下文，RetrievalMemory 负责召回。

## 不建议现在做

- 不建议立刻做 swarm/group agent。当前单 agent 工具 harness 还不够硬，多 agent 只会放大问题。
- 不建议先堆 20 个内置工具。先把 3 到 5 个核心工具做成生产级。
- 不建议把大量安全规则写进 prompt 代替 hard gate。规则应该在 policy/pipeline 中执行。
- 不建议直接实现私有 ChatGPT/Codex HTTP 协议。Codex 登录态继续通过 Codex CLI adapter 复用官方请求路径。
