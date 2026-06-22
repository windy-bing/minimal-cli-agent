# minimal-cli-agent

语言：[English](README.md) | 中文

一个受 [Minimal AI agent tutorial](https://minimal-agent.com/) 启发的极简 CLI Agent。它实现了文章里的核心循环：让模型输出一个 action，解析 action，在终端环境里执行，再把 observation 追加回上下文，持续循环。

项目刻意从小处开始，但代码按可替换模块拆分，后续可以演进到 sub-agent、group session、memory、权限、skills、MCP、plugins 和 workflow 委托。

![minimal-cli-agent Codex profile 运行效果](docs/assets/codex-profile.png)

## 当前能力

- 作为终端 CLI 运行。
- 支持 `--interactive` 多轮交互会话；不传 task 时也会进入同一个 REPL。
- 默认支持本地 Ollama chat 模型。
- 支持 Ollama、Codex CLI 登录态、Claude/Anthropic、Gemini profile。
- 支持直接指定 OpenAI-compatible `/chat/completions` 接口。
- 解析唯一一个 action，格式如下：

````text
```bash-action
ls -la
```
````

- 执行命令时带超时控制和非交互环境变量。
- 支持产品化权限模式：`default`、`autoEdit`、`plan`、`yolo`。
- 传入 `--session` 时，可以把 session messages 持久化到 JSON。
- transcript 变大时，会应用一个简单的本地上下文压缩保护。
- 暴露无状态 API：`Agent.chat_stream(message, context)`，以事件流形式产出 loop event。
- Agent loop 运行在 `AgentHarness` 边界后面，tools、memory、policy、context、environment 可以独立演进。

## 为什么这样开始

参考文章的关键观点是：一个有用的 CLI Agent 一开始不需要很大的框架。最小循环已经足够产生真实行为：

1. 保存 messages。
2. 请求语言模型。
3. 解析模型要求的 action。
4. 执行 action。
5. 把命令输出作为 observation 返回给模型。

这个仓库把循环保留在 `src/minimal_cli_agent/agent.py`，同时把 model、parser、environment、memory、policy、tool pipeline 拆开，方便后续替换。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

项目已包含 `httpx[socks]`，模型请求可以读取 `http_proxy`、`https_proxy`、`all_proxy` 中的 SOCKS 代理配置。

## 使用 Ollama 运行

```bash
ollama pull qwen3:4b
ollama serve
minimal-agent --permission default "List the files in this project, then exit"
```

等价的 module 方式：

```bash
python -m minimal_cli_agent.cli --permission default "List the files in this project, then exit"
```

## 只规划不执行命令

```bash
minimal-agent --permission plan "Inspect this repository structure"
```

## 多轮交互会话

启动一个多轮 CLI 会话：

```bash
minimal-agent --profile codex --permission plan --interactive
```

也可以先传入第一句话，然后继续对话：

```bash
minimal-agent --profile codex --permission plan --interactive "Analyze this project"
```

输入 `/exit` 或 `/quit` 退出。如果传入 `--session path/to/session.json`，每轮结束后会保存 messages，下次运行时继续加载。

## OpenAI-Compatible 接口

```bash
AGENT_PROVIDER=openai-compatible \
AGENT_BASE_URL=https://api.openai.com/v1 \
AGENT_API_KEY=... \
AGENT_MODEL=gpt-4.1-mini \
minimal-agent --permission default "Check the tests and summarize failures"
```

## Profiles

可以用 `--profile` 读取常见 CLI 模型工具的默认本地配置：

```bash
minimal-agent --profile ollama "List files, then exit"
minimal-agent --profile codex "List files, then exit"
minimal-agent --profile claude "List files, then exit"
minimal-agent --profile gemini "List files, then exit"
```

Profile 行为：

- `ollama`：读取 `OLLAMA_MODEL` 和 `OLLAMA_BASE_URL`，默认走本地 Ollama。
- `codex`：读取 `~/.codex/config.toml` 里的模型。如果显式设置了 `OPENAI_API_KEY` 或 `OPENAI_BASE_URL`，则使用 OpenAI-compatible provider。否则当 `~/.codex/auth.json` 包含 Codex 登录态 `tokens.access_token` 时，会使用本机 Codex CLI 作为请求适配器，不会把这个 token 发到 `api.openai.com`。
- `claude`：读取 `~/.claude/settings.json` 里的 model 和 Anthropic proxy env，同时支持 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`。
- `gemini`：读取 `GEMINI_MODEL`、`GEMINI_BASE_URL`、`GEMINI_API_KEY` 或 `GOOGLE_API_KEY`。

## CLI 参数

```text
--provider       ollama、openai-compatible、anthropic、gemini 或 codex
--profile        ollama、codex、claude 或 gemini
--model          模型名称
--base-url       provider base URL
--api-key        OpenAI-compatible 接口的 API key
--cwd            命令执行目录
--max-steps      Agent loop 最大迭代次数
--timeout        命令超时时间，单位秒
--interactive    启动多轮交互 CLI 会话
--permission     default、autoEdit、plan 或 yolo
--session        用于持久化 messages 的 JSON 文件
```

## 项目结构

```text
src/minimal_cli_agent/
  agent.py         控制循环，无状态 chat/chat_stream 入口
  harness.py       model、tools、memory、context、policy 的运行时边界
  interfaces.py    扩展点协议
  tool_registry.py 工具注册与发现边界
  tool_pipeline.py 分阶段工具执行管道
  policy.py        shell 权限策略
  context.py       上下文准备边界
  model.py         Ollama、OpenAI-compatible、Anthropic、Gemini HTTP client 和 Codex CLI adapter
  parser.py        bash-action 解析器
  environment.py   本地 shell 执行
  memory.py        JSON session store 和基础上下文压缩
  prompts.py       system prompt 和格式提醒
```

## 扩展计划

更完整的架构说明见 [docs/architecture.md](docs/architecture.md)。

- 基于模型总结的上下文压缩。
- explorer、worker、verifier 等 SubAgent。
- 多 Agent 协同的 GroupSession。
- 分层 memory 管理。
- 带审计记录的安全和权限策略。
- Skill、MCP 和 plugin 注册。
- `plan`、`delegate`、`wait`、`merge`、`verify` 等 workflow 委托原语。

## 边界状态

已实现：

- 无状态 `Agent.chat_stream(message, context)` 入口。
- 用于 UI/CLI 集成的 `LoopEvent` / `LoopResult`。
- `ToolRegistry` 和分阶段 `ToolExecutionPipeline`。
- `ToolDecision`：`allow`、`ask`、`deny`、`skip`。
- 产品权限模式：`default`、`autoEdit`、`plan`、`yolo`。

已预留但保持最小实现：

- 上下文压缩目前是本地截断，不是模型总结。
- `autoEdit` 目前和 `default` 类似，因为还没有文件编辑工具。
- session 持久化目前是 JSON，不是完整 event log。

暂不实现：

- 单轮多工具调用。
- 并发工具执行和文件锁。
- SubAgent 和 GroupSession runtime。
- MCP、plugin、skill 自动发现。
- workflow scheduler 或 delegation engine。

## 参考文章保留的实践

- 使用明确 action 格式，而不是猜测模型意图。
- 把命令输出作为 observation 返回给模型。
- 把超时、格式错误、权限拒绝作为可恢复 observation。
- 设置 `PAGER=cat`、`PIP_PROGRESS_BAR=off` 等非交互环境变量。
- model 和 environment 分离，方便替换。
