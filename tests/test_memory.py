import unittest

from minimal_cli_agent.memory import compact_messages
from minimal_cli_agent.types import Message


class MemoryTest(unittest.TestCase):
    def test_compact_messages_keeps_system_and_tail(self) -> None:
        messages = [Message("system", "s" * 10)]
        messages += [Message("user", str(i) * 20) for i in range(20)]

        compacted = compact_messages(messages, max_chars=80)

        self.assertEqual(compacted[0].role, "system")
        self.assertIn("compacted", compacted[1].content)
        self.assertEqual(compacted[-1].content, "19" * 20)


if __name__ == "__main__":
    unittest.main()
