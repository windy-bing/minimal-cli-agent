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
| 工具执行管道 | 已有 `ToolExecutionPipeline` 阶段形状，`ResolveDecision` 已有 decision hook 仲裁基线，但 `AutoVerify` 很薄 | 补 hook 优先级、冲突报告、确认 UI 适配、重试和格式化策略 |
| 权限 hard gate | 已有 `ShellPermissionPolicy`、`ToolDecision`、`plan` 跳过执行、`yolo` 仍受硬拒绝规则限制；policy 文件支持命令 allow 前缀、追加 deny token 和写入 allow/deny 路径范围 | 后续增加更丰富的策略报告、角色化能力和可查询审计日志 |
| 上下文压缩 | 当前是本地裁剪加提示，不是语义压缩 | 增加模型总结、原始 transcript 保留、可召回 summary/memory |
| Memory 管理 | JSON session 已支持 lock 保护、原子写、最近消息裁剪、active plan、typed workflow state 和 `/events` 最近事件查询 | 后续实现 SQLite session log 和 memory retrieval |
| SubAgent / GroupSession | 已有 read-only `SubAgentRunner` 和隔离 session；GroupSession 仍预留 | 后续补 worker/verifier 角色、写入合并策略和 group event log |
| Workflow 委托 | 已有 `/workflow create/step/done/show/clear` typed workflow state 和 `/delegate` 子代理委托 | 后续补 wait/merge/verify 和 scheduler |
| 并发和文件锁 | 已支持单轮多 action 串行执行、同进程同文件写锁和 `.agent/locks` 跨进程文件写锁，但明确暂不并发 | 后续再做读写分桶、并发工具执行、取消和超时传播 |
| MCP / plugin / skill | MCP 挂在 `ToolRegistry` 后面；`/skills` 可发现并加载工作区 skills | 后续定义 plugin/tool manifest，再接 MCP/plugin discovery |

## 未规划未实现

| 主题 | 风险 | 建议 |
| --- | --- | --- |
| 完整 JSON Schema | 已有聚焦 JSON Schema 子集，支持 nested object、array、enum、oneOf/anyOf、边界约束和字段级 repair observation | 后续补默认值、schema 文档生成和更完整 Draft 兼容 |
| 工具模糊识别 | 已在 Discovery 阶段返回安全的相近工具名建议，但不会自动执行猜测 | 后续可加入风险等级过滤和更细的提示文案 |
| 文件读取工具性能 | 已实现 `read_tail` / `read_forward` 基线，`read_forward` 支持 byte/line 双模式，读文件 observation 会带分页 metadata，并会拒绝疑似二进制文件 | 后续补编码策略、二进制专用摘要和持久分页游标 |
| grep/search top-k | 已实现 `search(pattern,path,top_k,max_files,timeout_ms)`，支持额外 ignore dirs、extension filter、项目 ignore 文件解析和相关性 ranking | 后续补渐进输出和更丰富的 ranking 特征 |
| 结构化文本编辑校验 | `write_file`/`edit_file` 已有 JSON/TOML/XML 写入前校验，YAML 在 PyYAML 可用时校验；还没有 schema 级校验和自动格式化 | 下一步补 JSON Schema、YAML schema、格式化建议和字段级 repair observation |
| Plan Mode 上下文隔离 | `/plan` 已使用独立 context；execute 阶段会读取 active plan，并在计划包含路径时约束 writer 工具路径 | 后续补 typed workflow state 和更细 tool allowlist |
| OS shell adapter | 已有 `ShellAdapter`，支持 system/bash/zsh/sh/powershell/cmd/git-bash，并在 observation 暴露 shell/cwd/encoding/path separator | 后续补真实 Windows 环境集成测试和更细 path rules |
| 环境变量刷新 | 长会话里环境变化不能被 runtime 感知 | Environment 每次执行前重建 env snapshot，并记录差异或允许 hook 更新 |
| 编码和换行 | Windows/codepage/二进制输出可能破坏 observation | 统一 stdout/stderr decoding 策略，保留 raw bytes 截断摘要 |
| 提示词预算治理 | 工具说明堆进 system prompt 会挤占上下文并制造 lost-in-the-middle | Prompt 应该按角色和可用工具动态生成，工具文档短描述默认注入，长说明按需召回 |
| AGENTS/项目规则注入治理 | 项目规则可能臃肿、冲突或被提示注入污染 | 项目规则需要分层、去重、冲突检测、来源标注和预算上限 |

## 已由当前设计规避

| 问题 | 当前设计优势 |
| --- | --- |
| Agent 持有 session 导致多会话混乱 | `Agent.chat_stream(message, context)` 是无状态入口，`ChatContext` 由调用方传入 |
| 工具调用散落在 Agent loop | `Agent` 只解析 action，执行经过 `AgentHarness`、`ToolRegistry`、`ToolExecutionPipeline` |
| 工具拼写错误直接失败 | Discovery observation 会返回 `suggested_tools`，但不会自动执行模糊匹配结果 |
| 文件工具整文件读取风险 | 已有 `read_tail`、`read_forward` 和 `search` 的有界基线，避免所有读取都走 `read_file` |
| Plan Mode 只靠模型自觉不执行 | 当前 `plan` 权限模式在 policy/pipeline 层返回 `skip`，shell 不会执行 |
| 权限模式只是提示词约定 | `ToolDecision` 是代码层决策，`deny/skip` 是 hard gate |
| Codex 登录态误走 Platform API | `codex` profile 检测 Codex auth 后走 Codex CLI adapter，不把 `tokens.access_token` 发到 `api.openai.com` |
| 提示词过早膨胀 | 当前只有极小 system prompt，并只注入 `bash-action` / `tool-action` 的短格式示例，没有把大量工具教程塞入 prompt |
| 边界不透明 | README/architecture 已拆出 implemented/reserved/not implemented |

## 优于当前设计、应吸收

| 能力 | 为什么优先 |
| --- | --- |
| 完整 schema validation + repair observation | 已有聚焦 JSON Schema 子集和字段级 repair observation；后续补 schema 文档生成 |
| Plan/Execute 双上下文 | 已有 `/plan` 隔离 context 和 typed plan artifact；execute 阶段会消费 active plan 并约束已知写入路径 |
| 文件工具流式读取和搜索 top-k | 已有 timeout、显式 ignore/filter、项目 ignore 文件解析、line/byte 分页 metadata 和搜索 ranking；后续补跨 turn 分页游标 |
| 结构化编辑校验 | 已有写入前 parse validation 基线，下一步做 schema/format/repair 增强 |
| ShellAdapter 跨平台设计 | 已有 ShellAdapter 基线；仍需补真实 Windows/Git Bash 行为验证 |
| Prompt budget policy | harness 不只管工具和权限，也要管提示词预算、角色差异和规则注入 |
| EventStore + memory retrieval | 压缩后无法召回会让长期任务丢状态；需要原始日志和可检索 memory 双轨 |

## 新方向

### Phase 1: Tool Harness Hardening

目标是让工具错误可恢复、可审计、可验证。

- `ToolSpec` 已支持聚焦 JSON Schema 子集；下一步补风险等级、输出 schema、默认值提示和 schema 文档生成。
- `Validation` 阶段已返回可恢复 observation 和字段级错误。
- `Formatting` 阶段已有统一 observation 基线，包含 `status`、`exit_code`、`command` 和 `output`；下一步补输出 schema 和机器可解析事件。
- `ToolExecutionPipeline` 已增加 decision hook 仲裁和测试覆盖；下一步补 hook 优先级、冲突报告和审计事件。

### Phase 2: File Tool Baseline

目标是先做少量高质量内置工具，而不是堆工具数量。

- `read_tail(path, lines, max_bytes)` 已使用尾部窗口读取，不整文件读入。
- `read_forward(path, offset, limit)` 已支持 byte offset 分页和最大输出；`mode:"lines"` 支持按行分页并返回下一页 offset。
- `search(pattern, path, top_k, max_files, timeout_ms)` 已有 top-k、文件数、超时、额外忽略目录、扩展名过滤、`.gitignore` / `.agentignore` 解析和相关性 ranking。
- `write_file` 和 `edit_file` 已对 JSON/TOML/XML 做 parse validation，YAML 在 PyYAML 可用时校验；下一步补 schema validation 和自动格式化。

### Phase 3: Plan/Execute Separation

目标是让 Plan Mode 变成真正的 harness 能力。

- `/plan` 阶段只使用 `plan` 权限和只读工具，产出 typed plan artifact。
- `/plan` 已使用独立 context 和 `plan` 权限生成 typed plan artifact。
- plan transcript 不直接并入 execute context，只保留计划摘要、步骤和必要证据。
- execute 阶段会读取 active plan 并注入 system prompt；当计划包含明确路径时，writer 工具只能写这些计划路径。
- 下一步补 typed workflow state 和更细粒度 tool allowlist。

### Phase 4: Cross-platform Environment

目标是让 shell 执行不再假设 bash。

- 已增加 `ShellAdapter` 接口。
- 支持 system/bash/zsh/sh/powershell/cmd/git-bash。
- observation 中明确 shell kind、cwd、encoding、path separator。
- 每次执行前刷新 env snapshot，并做 bytes 输出解码处理。

### Phase 5: Prompt and Memory Governance

目标是把 prompt 也纳入 harness，而不是无限堆 system text。

- 按 agent role 生成短 system prompt。
- 工具说明短描述默认注入，长说明按需召回。
- AGENTS/项目规则分层、去重、冲突检测、预算上限。
- JSON session event log 保存最近可查询事件；后续 EventStore 保存完整 transcript，WorkingMemory 只保存压缩上下文，RetrievalMemory 负责召回。

## 不建议现在做

- 不建议立刻做 swarm/group agent。当前单 agent 工具 harness 还不够硬，多 agent 只会放大问题。
- 不建议先堆 20 个内置工具。先把 3 到 5 个核心工具做成生产级。
- 不建议把大量安全规则写进 prompt 代替 hard gate。规则应该在 policy/pipeline 中执行。
- 不建议直接实现私有 ChatGPT/Codex HTTP 协议。Codex 登录态继续通过 Codex CLI adapter 复用官方请求路径。
