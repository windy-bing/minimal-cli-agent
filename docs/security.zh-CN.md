# 安全模型

## 默认边界

- `plan` 模式只允许只读工具，shell 和写入工具会被跳过。
- `default` 模式会询问 shell、写入和 MCP tool 调用。
- `autoEdit` 自动允许文件写入，但 shell 仍需确认。
- `yolo` 适合隔离沙箱，不适合直接在敏感工作区使用。

## 策略文件

使用 `--policy-file` 限定写入范围和命令前缀：

```json
{
  "allow_command_prefixes": ["pytest ", "python -m pytest "],
  "write_allow_paths": ["src/**", "tests/**", "docs/**"],
  "write_deny_paths": ["**/.env", "**/secrets*.py"],
  "sensitive_path_tokens": [".npmrc", ".pypirc"]
}
```

内置 hard gate 会继续阻止危险命令、敏感路径和未授权网络 shell 命令。

也可以启用更保守的内置预设：

```bash
minimal-agent --policy-preset strict --permission default
```

企业环境可以把允许的命令前缀放在单独文件中，并由 policy JSON 引用：

```json
{
  "allow_command_prefix_files": ["security/allowed-command-prefixes.txt"]
}
```

## 审计

- 权限决策、工具执行、MCP 注册和 session events 会进入 session store。
- `/events format=json` 可导出近期事件。
- `/metrics` 会汇总工具执行状态、权限决策、trace 数和 batch 平均耗时。
- `/debug bundle` 会生成脱敏诊断包，适合排障和审计留存。
