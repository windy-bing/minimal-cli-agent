# Codex 风格机制层改造清单

目标：减少对 prompt 纪律的依赖，把上下文、工具调用、预算和 UI trace 做成可测试机制。

## 已开始

- [x] 工具调用可见性：action-only 输出显示具体工具摘要。
- [x] 分页可见性：`read_forward` 摘要显示 offset/limit 或 line_offset/line_limit。
- [x] 上下文预算工具：新增 `get_context_remaining`，模型可查询当前 prompt 预算估算。
- [x] Turn 内工具调用 ledger：重复只读工具调用会被机制层跳过。
- [x] `read_forward` 分页保护：重复读取已覆盖范围时返回 repair observation，提示下一 offset。
- [x] Tool budget：每个 turn 限制总工具调用和只读工具调用；超过预算时返回 repair observation。
- [x] Observation compaction：UI 事件保留完整 observation，写回模型上下文的工具输出按预算压缩。
- [x] Runtime context fragment：每轮维护单条 runtime context 片段，旧片段会被替换而不是堆积。
- [x] Structured tool observations：写回模型上下文的工具结果使用 JSON schema，UI 仍保留文本 observation。
- [x] Tool call id trace：每个模型发起的工具调用都有 call_id，并贯穿 start/result/model observation。
- [x] Tool observation artifacts：被模型上下文压缩的大输出会保存到 `.agent/artifacts/` 并在 JSON observation 中引用。

## Codex 对照

- `context/*`：Codex 把权限、环境、skills、token budget 等拆成 typed context fragments；本项目已先实现 runtime context fragment，下一步应继续拆 `prompts.py` 和动态环境信息。
- `context/world_state/*`：Codex 用 snapshot/diff 只注入变化；本项目下一步应先覆盖 cwd、permission、model、plugins、skills。
- `context_manager/history.rs` 和 `normalize.rs`：Codex 维护结构化 history，补齐缺失 tool output 并移除孤儿 output；本项目已先做模型可见 JSON observation、call id 和 artifact 引用。
- `tools/router.rs` 和 `tools/orchestrator.rs`：Codex 将工具可见性、审批、沙箱、重试和 dispatch trace 分层；本项目已具备 registry/pipeline，下一步应补更完整 trace 和 sandbox attempt 语义。
- `tools/tool_dispatch_trace.rs`：Codex 将 tool dispatch 开始/结束结构化记录；本项目已补 call_id/requester 基础链路，下一步应补更完整 dispatch trace 和 raw artifact。
- `tools/handlers/get_context_remaining.rs` 与 `new_context_window.rs`：Codex 给模型显式上下文预算和新窗口工具；本项目已实现预算查询，下一步实现新窗口生命周期。

## 下一批

- [ ] Context fragments v2：把 system prompt、权限、环境、项目规则、预算、session 状态进一步拆成多类 typed fragments。
- [ ] World state diff：只在 cwd、权限、模型、插件、skills、项目规则变化时注入差异，避免每轮重复注入完整状态。
- [ ] Tool dispatch trace：把 tool start/result、预算跳过、权限决策和重试尝试串成可查询 trace。
- [ ] Context window lifecycle：支持显式开启新 context window，并把旧窗口状态以结构化摘要迁移。
- [ ] Integration traces：为重复读取、分页、预算、上下文压缩建立端到端测试，不只测单函数。

## 设计原则

- 模型负责推理和选择下一步，runtime 负责预算、去重、安全和上下文形状。
- 所有注入模型上下文的内容必须有硬上限。
- 所有工具结果必须有机器可读 metadata，UI 显示与模型可见内容分离。
- prompt 只描述能力和策略，不能作为唯一约束。
