import unittest
import json

from minimal_cli_agent.constants import Tools
from minimal_cli_agent.exceptions import AgentFinished, FormatError
from minimal_cli_agent.parser import parse_action


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

    def test_parse_action_exit(self) -> None:
        with self.assertRaises(AgentFinished):
            parse_action("```bash-action\nexit\n```")


if __name__ == "__main__":
    unittest.main()
