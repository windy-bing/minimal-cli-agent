import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from minimal_cli_agent.environment import LocalEnvironment, resolve_shell_adapter
from minimal_cli_agent.types import AgentConfig


class EnvironmentTest(unittest.TestCase):
    def test_execute_redacts_secret_output(self) -> None:
        environment = LocalEnvironment(AgentConfig(permission_mode="yolo"))

        result = environment.execute("printf 'OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\\n'")

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", result.output)
        self.assertIn("OPENAI_API_KEY=<redacted>", result.output)

    def test_shell_adapter_records_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            environment = LocalEnvironment(AgentConfig(cwd=Path(tmp), permission_mode="yolo", shell_kind="sh"))

            result = environment.execute("printf hello")

        self.assertEqual(result.output, "hello")
        self.assertEqual(result.metadata["shell"], "sh")
        self.assertEqual(result.metadata["encoding"], "utf-8")
        self.assertIn("cwd", result.metadata)

    def test_docker_sandbox_builds_isolated_command(self) -> None:
        class Completed:
            returncode = 0
            stdout = b"ok"

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig(
                cwd=root,
                permission_mode="yolo",
                sandbox_kind="docker",
                sandbox_image="python:3.11-slim",
                sandbox_network="none",
                sandbox_read_only=True,
            )
            with patch("subprocess.run", return_value=Completed()) as run_mock:
                result = LocalEnvironment(config).execute("python --version")

        argv = run_mock.call_args.args[0]
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, "ok")
        self.assertEqual(result.metadata["sandbox"], "docker")
        self.assertIn("--network", argv)
        self.assertIn("none", argv)
        self.assertIn(f"{root}:/workspace:ro", argv)
        self.assertIn("python:3.11-slim", argv)
        self.assertEqual(argv[-3:], ["sh", "-lc", "python --version"])

    def test_resolve_shell_adapter_supports_named_shells(self) -> None:
        self.assertEqual(resolve_shell_adapter("bash").argv("echo hi"), ["bash", "-lc", "echo hi"])
        self.assertEqual(resolve_shell_adapter("powershell").argv("Write-Output hi")[:3], ["pwsh", "-NoProfile", "-NonInteractive"])
        self.assertEqual(resolve_shell_adapter("cmd").path_separator, "\\")


if __name__ == "__main__":
    unittest.main()
