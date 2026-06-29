from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.memory import JsonSessionStore
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopOptions, Message


class FinalTextModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        return f"turn {self.calls} acknowledged"


class StabilityTest(unittest.TestCase):
    def test_long_interactive_like_session_keeps_context_and_session_writable(self) -> None:
        model = FinalTextModel()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonSessionStore(root / "session.json")
            config = AgentConfig(cwd=root, permission_mode="plan", max_context_chars=4000, summarize_context=False)
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=store))
            context = ChatContext()

            for index in range(60):
                result = agent.chat(f"message {index}", context, options=LoopOptions(allow_final_text=True))
                self.assertTrue(result.success)
                context.messages = result.final_messages
                store.save(context.messages)

            reloaded = store.load()

        self.assertEqual(model.calls, 60)
        self.assertLessEqual(len(reloaded), 200)
        self.assertIn("turn 60 acknowledged", reloaded[-1].content)


if __name__ == "__main__":
    unittest.main()
