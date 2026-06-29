# 生产部署建议

## 发布流程

- 版本号以 `pyproject.toml` 为准，tag 使用 `vX.Y.Z`。
- 每次发布前更新 `CHANGELOG.md`，跑完 `pytest`、`pyright`、`python -m build` 和 `twine check dist/*`。
- GitHub tag 会触发 `.github/workflows/release.yml`，构建 wheel/sdist、安装 wheel 做 smoke test，并通过 PyPI Trusted Publishing 发布。

## 运行配置

- 建议将默认配置写入项目 `.minimal-agent.json`，个人覆盖放入 `~/.minimal-agent/config.json`。
- 生产环境优先使用 `--session-db`，保留完整 transcript、events 和检索 memory。
- 为付费模型配置 `--usage-ledger`、token/cost limit 和 `usage_subject`/`usage_tenant`。
- 对远程 provider 配置 fallback route、`model_max_retries`、`model_max_concurrency` 和 circuit breaker 参数。
- 对共享或敏感工作区启用 `--policy-preset strict`，并通过 policy file 管理企业命令 allowlist。

## 交付前检查

```bash
minimal-agent --show-config --permission plan --no-session
minimal-agent --permission plan
/doctor
/metrics
/debug bundle .agent/debug-bundle.zip
```

`/doctor json` 适合接入外部健康检查或工单附件。
