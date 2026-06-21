import unittest

from minimal_cli_agent.exceptions import AgentFinished, FormatError
from minimal_cli_agent.parser import parse_action


class ParserTest(unittest.TestCase):
    def test_parse_action_codeblock(self) -> None:
        output = "I will inspect files.\n```bash-action\nls -la\n```"
        self.assertEqual(parse_action(output), "ls -la")

    def test_parse_action_requires_one_block(self) -> None:
        with self.assertRaises(FormatError):
            parse_action("no action")

    def test_parse_action_exit(self) -> None:
        with self.assertRaises(AgentFinished):
            parse_action("```bash-action\nexit\n```")


if __name__ == "__main__":
    unittest.main()
