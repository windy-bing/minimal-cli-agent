# Example

Run with local Ollama:

```bash
ollama pull qwen3:4b
ollama serve
python -m minimal_cli_agent.cli --permission default "List files in the current directory, then exit"
```

Run without executing commands:

```bash
python -m minimal_cli_agent.cli --permission plan "Inspect this project structure"
```
