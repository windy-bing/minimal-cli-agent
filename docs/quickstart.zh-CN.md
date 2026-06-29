# 快速上手

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

如果需要可复现的运行时依赖版本，可以使用：

```bash
pip install -c requirements.lock -e .
```

## 本地 Ollama

```bash
ollama pull qwen3:4b
ollama serve
minimal-agent
```

## 常用交互命令

```text
/help
/config explain
/doctor
/permission plan
/permission autoEdit
/session stats
/debug bundle .agent/debug-bundle.zip
```

交互终端支持 `/` 命令候选和常见参数候选，例如 `/permission ` 会提示可用权限模式。支持的终端还可以用 `Ctrl-R` 搜索历史，用 `Ctrl-J` 插入多行输入。
