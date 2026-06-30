# Codex 风格机制层改造清单

目标：减少对 prompt 纪律的依赖，把上下文、工具调用、预算和 UI trace 做成可测试机制。

## 已开始

- [x] 工具调用可见性：action-only 输出显示具体工具摘要。
- [x] 分页可见性：`read_forward` 摘要显示 offset/limit 或 line_offset/line_limit。
- [x] 上下文预算工具：新增 `get_context_remaining`，模型可查询当前 prompt 预算估算。
- [x] Turn 内工具调用 ledger：重复只读工具调用会被机制层跳过。
- [x] `read_forward` 分页保护：重复读取已覆盖范围时返回 repair observation，提示下一 offset。

## 下一批

- [ ] Context fragments：把 system prompt、权限、环境、项目规则、预算、session 状态拆成 typed fragments，而不是拼接大字符串。
- [ ] World state diff：只在 cwd、权限、模型、插件、skills、项目规则变化时注入差异，避免每轮重复注入完整状态。
- [ ] Observation compaction：工具输出拆成结构化 metadata、短摘要、raw artifact 引用，避免大文本直接进入 messages。
- [ ] Tool budget：每个 turn 限制连续 read/search 次数；超过预算时要求总结已知事实或请求用户确认继续。
- [ ] Context window lifecycle：支持显式开启新 context window，并把旧窗口状态以结构化摘要迁移。
- [ ] Integration traces：为重复读取、分页、预算、上下文压缩建立端到端测试，不只测单函数。

## 设计原则

- 模型负责推理和选择下一步，runtime 负责预算、去重、安全和上下文形状。
- 所有注入模型上下文的内容必须有硬上限。
- 所有工具结果必须有机器可读 metadata，UI 显示与模型可见内容分离。
- prompt 只描述能力和策略，不能作为唯一约束。
