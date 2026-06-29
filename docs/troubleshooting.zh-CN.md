# 故障排查

## 启动失败

1. 运行 `minimal-agent --show-config --no-session` 查看 provider、model、base_url 和 session 配置。
2. 运行 `/doctor` 检查 workspace、配置文件、session、model、policy、MCP 和 plugin。
3. 如果配置文件报 unknown key，按 `/config explain` 的优先级检查项目和用户配置。

## 模型请求失败

- OpenAI-compatible、Anthropic 和 Gemini 通常需要 API key。
- 本地 Ollama 需要确认 `ollama serve` 正在运行，且模型已 pull。
- 使用 fallback 时，检查每条 JSON route 是否包含 `provider`、`model`、`base_url`。

## 权限或工具被跳过

- `plan` 模式会跳过 shell 和写入工具，这是预期行为。
- 用 `/policy` 查看当前规则，用 `/policy explain <tool> <payload>` 查看具体决策。
- 需要写文件时切到 `/permission autoEdit`，需要执行命令时按提示确认。

## 诊断包

```text
/debug bundle .agent/debug-bundle.zip
```

诊断包包含脱敏后的 config、doctor、policy 和最近 events。
