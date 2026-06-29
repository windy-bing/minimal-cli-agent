# Provider 测试矩阵

这个项目默认单元测试不触达真实 provider。发布前建议用以下矩阵做手动或定时集成测试。

| Provider | 命令 | 前置条件 | 期望 |
| --- | --- | --- | --- |
| Ollama | `minimal-agent --profile ollama --permission plan "回复 ready"` | `ollama serve`，模型已 pull | 能返回自然语言 |
| OpenAI-compatible | `minimal-agent --provider openai-compatible --base-url "$BASE_URL" --api-key "$API_KEY" --model "$MODEL" --permission plan "回复 ready"` | 有兼容 `/chat/completions` 的服务 | 能返回自然语言 |
| Anthropic | `minimal-agent --profile claude --permission plan "回复 ready"` | `ANTHROPIC_API_KEY` 或本地 Claude 配置 | 能返回自然语言 |
| Gemini | `minimal-agent --profile gemini --permission plan "回复 ready"` | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | 能返回自然语言 |
| Codex | `minimal-agent --profile codex --permission plan "回复 ready"` | 本机 Codex CLI 登录态 | 能返回自然语言 |

## 长会话压力测试

本仓库包含纯本地长会话测试，覆盖多 turn context、session 写入和事件汇总：

```bash
python -m pytest tests/test_stability.py
```

真实 provider 压测建议限制预算：

```bash
minimal-agent \
  --session-db .agent/provider-stress.sqlite \
  --usage-ledger .agent/provider-usage.jsonl \
  --daily-cost-limit 1.00 \
  --model-max-retries 1 \
  --model-circuit-failure-threshold 2
```
