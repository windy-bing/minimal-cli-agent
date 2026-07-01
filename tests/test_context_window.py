import json
import unittest

from minimal_cli_agent.context_window import CONTEXT_WINDOW_SUMMARY_OPEN, build_context_window_summary, open_context_window
from minimal_cli_agent.types import Message


class ContextWindowTest(unittest.TestCase):
    def test_open_context_window_keeps_system_and_structured_summary(self) -> None:
        messages = [
            Message(role="system", content="system"),
            Message(role="user", content="implement feature"),
            Message(role="assistant", content='```tool-action\n{"tool":"read_file","path":"src/app.py"}\n```'),
            Message(role="user", content='Tool observation for model context:\n```json\n{"status":"success"}\n```'),
        ]

        new_messages, summary = open_context_window(messages)

        self.assertEqual([message.role for message in new_messages], ["system", "user"])
        self.assertEqual(new_messages[0].content, "system")
        self.assertIn(CONTEXT_WINDOW_SUMMARY_OPEN, new_messages[1].content)
        self.assertEqual(summary.task_goal, "implement feature")
        self.assertEqual(summary.files_read, ("src/app.py",))
        self.assertEqual(summary.source_message_count, 4)

    def test_context_window_summary_is_json_payload(self) -> None:
        summary = build_context_window_summary([Message(role="user", content="ship it")], extra_summary="tests passed")
        payload = json.loads(
            open_context_window([Message(role="user", content="ship it")], extra_summary="tests passed")[0][0]
            .content.split("\n", 1)[1]
            .rsplit("\n", 1)[0]
        )

        self.assertEqual(summary.task_goal, "ship it")
        self.assertIn("tests passed", payload["key_observations"])


if __name__ == "__main__":
    unittest.main()
