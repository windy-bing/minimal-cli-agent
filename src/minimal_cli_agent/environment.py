from __future__ import annotations

import os
import subprocess

from minimal_cli_agent.exceptions import CommandTimeout
from minimal_cli_agent.interfaces import PermissionPolicy
from minimal_cli_agent.types import AgentConfig, CommandResult

NON_INTERACTIVE_ENV = {
    "PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
}

class LocalEnvironment:
    def __init__(self, config: AgentConfig, permission_policy: PermissionPolicy | None = None) -> None:
        self.config = config
        self.permission_policy = permission_policy

    def execute(self, command: str) -> CommandResult:
        if self.permission_policy:
            self.permission_policy.check("shell", command)

        if self.config.permission_mode == "plan":
            return CommandResult(command=command, exit_code=0, output="plan mode: command not executed.", skipped=True)

        env = os.environ.copy() | NON_INTERACTIVE_ENV
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.config.cwd,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.config.command_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            raise CommandTimeout(
                f"Command timed out after {self.config.command_timeout}s.\nPartial output:\n{output}"
            ) from exc

        output = result.stdout[-self.config.max_output_chars :]
        return CommandResult(command=command, exit_code=result.returncode, output=output)
