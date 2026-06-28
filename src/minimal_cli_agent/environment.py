from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess

from minimal_cli_agent.constants import PermissionModes, SandboxKinds
from minimal_cli_agent.exceptions import CommandTimeout
from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import AgentConfig, CommandResult

NON_INTERACTIVE_ENV = {
    "PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
}


@dataclass(frozen=True)
class ShellAdapter:
    kind: str
    executable: str
    args_prefix: tuple[str, ...]
    encoding: str = "utf-8"
    path_separator: str = os.sep

    def argv(self, command: str) -> list[str]:
        return [self.executable, *self.args_prefix, command]

    def metadata(self, cwd: str) -> dict[str, str]:
        return {
            "shell": self.kind,
            "shell_executable": self.executable,
            "cwd": cwd,
            "encoding": self.encoding,
            "path_separator": self.path_separator,
        }


def resolve_shell_adapter(kind: str) -> ShellAdapter:
    normalized = kind.strip().lower() or "system"
    if normalized == "system":
        shell = os.environ.get("SHELL") or "/bin/sh"
        name = Path(shell).name.lower()
        if name in {"bash", "zsh", "sh"}:
            return ShellAdapter(kind=name, executable=shell, args_prefix=("-lc",))
        return ShellAdapter(kind="sh", executable="/bin/sh", args_prefix=("-lc",))
    if normalized in {"bash", "zsh", "sh"}:
        return ShellAdapter(kind=normalized, executable=normalized, args_prefix=("-lc",))
    if normalized in {"powershell", "pwsh"}:
        return ShellAdapter(kind="powershell", executable="pwsh", args_prefix=("-NoProfile", "-NonInteractive", "-Command"))
    if normalized == "cmd":
        return ShellAdapter(kind="cmd", executable="cmd", args_prefix=("/d", "/s", "/c"), encoding="mbcs", path_separator="\\")
    if normalized == "git-bash":
        return ShellAdapter(kind="git-bash", executable="bash", args_prefix=("-lc",))
    parts = shlex.split(kind)
    if not parts:
        return resolve_shell_adapter("system")
    return ShellAdapter(kind=Path(parts[0]).name, executable=parts[0], args_prefix=tuple(parts[1:]))


class LocalEnvironment:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.shell = resolve_shell_adapter(config.shell_kind)

    def execute(self, command: str) -> CommandResult:
        if self.config.permission_mode == PermissionModes.PLAN:
            return CommandResult(command=command, exit_code=0, output="plan mode: command not executed.", skipped=True)
        if self.config.sandbox_kind == SandboxKinds.DOCKER:
            return self._execute_docker(command)
        if self.config.sandbox_kind == SandboxKinds.HOST:
            return self._execute_host(command)
        return CommandResult(
            command=command,
            exit_code=2,
            output=f"Unknown command sandbox: {self.config.sandbox_kind}. Expected one of: {', '.join(SandboxKinds.ALL)}.",
        )

    def _execute_host(self, command: str) -> CommandResult:
        env = os.environ.copy() | NON_INTERACTIVE_ENV
        metadata = self.shell.metadata(str(self.config.cwd)) | {"sandbox": SandboxKinds.HOST}
        try:
            result = subprocess.run(
                self.shell.argv(command),
                shell=False,
                cwd=self.config.cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.config.command_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = redact_text(decode_output(exc.stdout, self.shell.encoding))[-self.config.max_output_chars :]
            raise CommandTimeout(
                f"Command timed out after {self.config.command_timeout}s.\nPartial output:\n{output}"
            ) from exc

        output = redact_text(decode_output(result.stdout, self.shell.encoding))[-self.config.max_output_chars :]
        return CommandResult(command=command, exit_code=result.returncode, output=output, metadata=metadata)

    def _execute_docker(self, command: str) -> CommandResult:
        shell = container_shell_adapter(self.config.shell_kind)
        mount_mode = "ro" if self.config.sandbox_read_only else "rw"
        workspace_mount = f"{self.config.cwd}:/workspace:{mount_mode}"
        metadata = shell.metadata("/workspace") | {
            "sandbox": SandboxKinds.DOCKER,
            "sandbox_image": self.config.sandbox_image,
            "sandbox_network": self.config.sandbox_network,
            "sandbox_read_only": str(self.config.sandbox_read_only),
            "workspace_mount": workspace_mount,
        }
        env_args = [item for key, value in NON_INTERACTIVE_ENV.items() for item in ("-e", f"{key}={value}")]
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            self.config.sandbox_network,
            "-v",
            workspace_mount,
            "-w",
            "/workspace",
            *env_args,
            self.config.sandbox_image,
            *shell.argv(command),
        ]
        try:
            result = subprocess.run(
                argv,
                shell=False,
                cwd=self.config.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.config.command_timeout,
            )
        except FileNotFoundError:
            return CommandResult(
                command=command,
                exit_code=127,
                output="docker executable not found; install Docker or switch to --sandbox host.",
                metadata=metadata,
            )
        except subprocess.TimeoutExpired as exc:
            output = redact_text(decode_output(exc.stdout, shell.encoding))[-self.config.max_output_chars :]
            raise CommandTimeout(
                f"Command timed out after {self.config.command_timeout}s in Docker sandbox.\nPartial output:\n{output}"
            ) from exc

        output = redact_text(decode_output(result.stdout, shell.encoding))[-self.config.max_output_chars :]
        return CommandResult(command=command, exit_code=result.returncode, output=output, metadata=metadata)


def decode_output(value: bytes | str | None, encoding: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.decode(encoding, errors="replace")
    except LookupError:
        return value.decode("utf-8", errors="replace")


def container_shell_adapter(kind: str) -> ShellAdapter:
    normalized = kind.strip().lower() or "system"
    if normalized == "system":
        return resolve_shell_adapter("sh")
    adapter = resolve_shell_adapter(normalized)
    if adapter.executable.startswith("/") and Path(adapter.executable).name in {"bash", "zsh", "sh"}:
        return ShellAdapter(adapter.kind, Path(adapter.executable).name, adapter.args_prefix, adapter.encoding, adapter.path_separator)
    return adapter
