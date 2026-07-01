import json
import unittest

from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import CommandResult


class RedactionTest(unittest.TestCase):
    def test_redacts_common_secret_shapes(self) -> None:
        text = "\n".join(
            [
                "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
                "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890",
                "token='eyJaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbb.ccccccccccccccccc'",
                "GEMINI_API_KEY=AIzaabcdefghijklmnopqrstuvwxyz",
            ]
        )

        redacted = redact_text(text)

        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234567890", redacted)
        self.assertNotIn("eyJaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbb.ccccccccccccccccc", redacted)
        self.assertNotIn("AIzaabcdefghijklmnopqrstuvwxyz", redacted)
        self.assertIn("OPENAI_API_KEY=<redacted>", redacted)
        self.assertIn("Authorization: <redacted>", redacted)

    def test_short_jwt_like_text_is_not_redacted(self) -> None:
        text = "token eyJshortvalue.alsoshort.thirdshort"

        self.assertEqual(redact_text(text), text)

    def test_redacts_secret_url_query_parameters(self) -> None:
        text = "https://example.test/path?key=AIzaabcdefghijklmnopqrstuvwxyz&token=super-secret-token&safe=ok"

        redacted = redact_text(text)

        self.assertNotIn("AIzaabcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("super-secret-token", redacted)
        self.assertIn("key=<redacted>", redacted)
        self.assertIn("token=<redacted>", redacted)
        self.assertIn("safe=ok", redacted)

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

    def test_command_result_observation_has_structured_fields(self) -> None:
        result = CommandResult(command="echo hello", exit_code=0, output="hello")

        observation = result.as_observation()

        self.assertIn("Command finished with exit code 0:", observation)
        self.assertIn("status: success", observation)
        self.assertIn("exit_code: 0", observation)
        self.assertIn("command:\n```text\necho hello\n```", observation)
        self.assertIn("output:\n```text\nhello\n```", observation)

    def test_skipped_command_result_observation_has_structured_fields(self) -> None:
        result = CommandResult(command="echo hello", exit_code=0, output="plan mode", skipped=True)

        observation = result.as_observation()

        self.assertIn("Command skipped:", observation)
        self.assertIn("status: skipped", observation)
        self.assertIn("output:\n```text\nplan mode\n```", observation)

    def test_command_result_can_compact_output_for_model_context(self) -> None:
        result = CommandResult(command="read_file big.txt", exit_code=0, output="a" * 80 + "MIDDLE" + "z" * 80)

        observation = result.as_observation(output_limit=60)

        self.assertIn("model_output_truncated: true", observation)
        self.assertIn("truncated for model context", observation)
        self.assertIn("aaa", observation)
        self.assertIn("zzz", observation)
        self.assertLess(len(observation), len(result.as_observation()))

    def test_command_result_model_observation_is_structured_json(self) -> None:
        result = CommandResult(command="read_file big.txt", exit_code=0, output="a" * 80 + "MIDDLE" + "z" * 80, metadata={"path": "big.txt"})

        observation = result.as_model_observation(output_limit=60, call_id="call-1")
        payload = json.loads(observation.split("```json\n", 1)[1].rsplit("\n```", 1)[0])

        self.assertEqual(payload["schema"], "minimal_cli_agent.tool_observation.v1")
        self.assertEqual(payload["call_id"], "call-1")
        self.assertEqual(payload["requester"], "model")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["metadata"], {"path": "big.txt"})
        self.assertTrue(payload["output_truncated_for_model"])
        self.assertIn("truncated for model context", payload["output"])
        self.assertNotIn("MIDDLE", payload["output"])


if __name__ == "__main__":
    unittest.main()
