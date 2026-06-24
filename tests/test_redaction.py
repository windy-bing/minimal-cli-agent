import unittest

from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import CommandResult


class RedactionTest(unittest.TestCase):
    def test_redacts_common_secret_shapes(self) -> None:
        text = "\n".join(
            [
                "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
                "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890",
                "token='eyJaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc'",
                "GEMINI_API_KEY=AIzaabcdefghijklmnopqrstuvwxyz",
            ]
        )

        redacted = redact_text(text)

        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234567890", redacted)
        self.assertNotIn("eyJaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc", redacted)
        self.assertNotIn("AIzaabcdefghijklmnopqrstuvwxyz", redacted)
        self.assertIn("OPENAI_API_KEY=<redacted>", redacted)
        self.assertIn("Authorization: <redacted>", redacted)

    def test_command_result_redacts_command_and_output_observation(self) -> None:
        result = CommandResult(
            command="printf sk-abcdefghijklmnopqrstuvwxyz123456",
            exit_code=0,
            output="TOKEN=super-secret-token",
            skipped=True,
        )

        observation = result.as_observation()

        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", observation)
        self.assertNotIn("super-secret-token", observation)
        self.assertIn("<redacted", observation)


if __name__ == "__main__":
    unittest.main()
