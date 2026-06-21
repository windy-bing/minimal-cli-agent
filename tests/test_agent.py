import unittest

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.types import AgentConfig, ChatContext, Message


class FakeModel:
    def complete(self, messages: list[Message]) -> str:
        return "Done.\n```bash-action\nexit\n```"


class AgentTest(unittest.TestCase):
    def test_chat_stream_is_context_driven(self) -> None:
        config = AgentConfig(permission_mode="plan")
        harness = AgentHarness(config=config, model=FakeModel())
        agent = Agent(config=config, harness=harness)

        stream = agent.chat_stream("finish", ChatContext(session_id="s1"))
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as exc:
                result = exc.value
                break

        self.assertTrue(result.success)
        self.assertEqual(events[0].type, "step_start")
        self.assertEqual(events[-1].type, "done")


if __name__ == "__main__":
    unittest.main()

