import json
import unittest

from minimal_cli_agent.constants import Tools
from minimal_cli_agent.tool_ledger import ToolCallLedger
from minimal_cli_agent.types import CommandResult, ToolCall


class ToolCallLedgerTest(unittest.TestCase):
    def test_skips_repeated_successful_read_only_call(self) -> None:
        ledger = ToolCallLedger()
        call = ToolCall(name=Tools.SEARCH, payload=json.dumps({"pattern": "README", "path": "."}))

        allowed, skipped = ledger.filter_before_execution([call])
        ledger.record_result(call, CommandResult(command="search .", exit_code=0, output="README.md"))
        allowed_again, skipped_again = ledger.filter_before_execution([call])

        self.assertEqual(allowed, [call])
        self.assertEqual(skipped, [])
        self.assertEqual(allowed_again, [])
        self.assertEqual(len(skipped_again), 1)
        self.assertIn("Repeated read-only tool call skipped", skipped_again[0].result.output)

    def test_skips_read_forward_before_previous_next_offset(self) -> None:
        ledger = ToolCallLedger()
        first = ToolCall(name=Tools.READ_FORWARD, payload=json.dumps({"path": "src/app.py", "offset": 0, "limit": 100}))
        repeated = ToolCall(name=Tools.READ_FORWARD, payload=json.dumps({"path": "src/app.py", "offset": 0, "limit": 100}))

        ledger.record_result(first, CommandResult(command="read_forward src/app.py", exit_code=0, output="x", metadata={"next_offset": 100}))
        allowed, skipped = ledger.filter_before_execution([repeated])

        self.assertEqual(allowed, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("next unread offset is 100", skipped[0].result.output)
        self.assertEqual(skipped[0].result.metadata["next_offset"], 100)


if __name__ == "__main__":
    unittest.main()
