import unittest
import json

from minimal_cli_agent.constants import Tools
from minimal_cli_agent.exceptions import AgentFinished, FormatError
from minimal_cli_agent.parser import parse_action, parse_actions


class ParserTest(unittest.TestCase):
    def test_parse_action_codeblock(self) -> None:
        output = "I will inspect files.\n```bash-action\nls -la\n```"
        call = parse_action(output)
        self.assertEqual(call.name, Tools.SHELL)
        self.assertEqual(call.payload, "ls -la")

    def test_parse_tool_action_codeblock(self) -> None:
        output = 'Read it.\n```tool-action\n{"tool":"read_file","path":"README.md"}\n```'
        call = parse_action(output)
        self.assertEqual(call.name, Tools.READ_FILE)
        self.assertEqual(json.loads(call.payload), {"path": "README.md"})

    def test_parse_action_requires_one_block(self) -> None:
        with self.assertRaises(FormatError):
            parse_action("no action")

    def test_parse_action_rejects_multiple_blocks(self) -> None:
        with self.assertRaises(FormatError):
            parse_action("```bash-action\nls\n```\n```tool-action\n{\"tool\":\"read_file\",\"path\":\"README.md\"}\n```")

    def test_parse_actions_accepts_multiple_blocks_in_order(self) -> None:
        calls = parse_actions(
            "```tool-action\n{\"tool\":\"read_file\",\"path\":\"README.md\"}\n```\n"
            "```bash-action\nls -la\n```"
        )

        self.assertEqual([call.name for call in calls], [Tools.READ_FILE, Tools.SHELL])
        self.assertEqual(json.loads(calls[0].payload), {"path": "README.md"})
        self.assertEqual(calls[1].payload, "ls -la")

    def test_parse_actions_reports_invalid_json_detail(self) -> None:
        with self.assertRaisesRegex(FormatError, "tool-action JSON is invalid"):
            parse_actions('```tool-action\n{"tool":"read_file","path":\n```')

    def test_parse_actions_reports_missing_tool_field(self) -> None:
        with self.assertRaisesRegex(FormatError, "non-empty string field"):
            parse_actions('```tool-action\n{"path":"README.md"}\n```')

    def test_parse_actions_rejects_exit_mixed_with_other_actions(self) -> None:
        with self.assertRaisesRegex(FormatError, "exit must be the only action"):
            parse_actions("```bash-action\nexit\n```\n```bash-action\nls\n```")

    def test_parse_action_exit(self) -> None:
        with self.assertRaises(AgentFinished):
            parse_action("```bash-action\nexit\n```")


if __name__ == "__main__":
    unittest.main()
