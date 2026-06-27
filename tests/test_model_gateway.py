import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minimal_cli_agent.exceptions import ModelRequestError
from minimal_cli_agent.model_gateway import ModelGateway, UsageLedger, estimate_tokens
from minimal_cli_agent.types import AgentConfig, Message, ModelRoute


class FactoryModel:
    def __init__(self, output: str | Exception, calls: list[str], model_name: str) -> None:
        self.output = output
        self.calls = calls
        self.model_name = model_name

    def complete(self, messages: list[Message]) -> str:
        self.calls.append(self.model_name)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class ModelGatewayTest(unittest.TestCase):
    def test_gateway_falls_back_after_primary_failure(self) -> None:
        calls: list[str] = []
        outputs = {
            "primary": ModelRequestError("primary down"),
            "fallback": "fallback ok",
        }

        def factory(config: AgentConfig) -> FactoryModel:
            return FactoryModel(outputs[config.model], calls, config.model)

        config = AgentConfig(
            model="primary",
            model_fallbacks=(ModelRoute(provider="ollama", model="fallback", base_url="http://fallback"),),
        )
        gateway = ModelGateway(config, model_factory=factory)

        output = gateway.complete([Message(role="user", content="hello")])

        self.assertEqual(output, "fallback ok")
        self.assertEqual(calls, ["primary", "fallback"])

    def test_gateway_retries_before_fallback(self) -> None:
        calls: list[str] = []
        remaining_failures = {"primary": 1}

        def factory(config: AgentConfig) -> FactoryModel:
            if remaining_failures.get(config.model, 0):
                remaining_failures[config.model] -= 1
                return FactoryModel(ModelRequestError("temporary"), calls, config.model)
            return FactoryModel("recovered", calls, config.model)

        gateway = ModelGateway(AgentConfig(model="primary", model_max_retries=1), model_factory=factory)

        self.assertEqual(gateway.complete([Message(role="user", content="hello")]), "recovered")
        self.assertEqual(calls, ["primary", "primary"])

    def test_gateway_rejects_request_over_token_limit(self) -> None:
        gateway = ModelGateway(AgentConfig(max_input_tokens=1), model_factory=lambda config: FactoryModel("ok", [], config.model))

        with self.assertRaises(ModelRequestError):
            gateway.complete([Message(role="user", content="this input is too long")])

    def test_gateway_records_usage_to_jsonl(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "usage.jsonl"
            config = AgentConfig(
                usage_ledger_path=ledger_path,
                usage_subject="user-1",
                usage_tenant="tenant-1",
                model_price_input_per_1m=1.0,
                model_price_output_per_1m=2.0,
            )
            gateway = ModelGateway(config, model_factory=lambda route_config: FactoryModel("hello", [], route_config.model))

            gateway.complete([Message(role="user", content="hello")])

            records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["subject"], "user-1")
        self.assertEqual(records[0]["tenant"], "tenant-1")
        self.assertEqual(records[0]["status"], "success")
        self.assertGreaterEqual(records[0]["total_tokens"], estimate_tokens("hello"))
        self.assertGreater(records[0]["estimated_cost"], 0)

    def test_gateway_opens_circuit_and_skips_unhealthy_route(self) -> None:
        calls: list[str] = []

        def factory(config: AgentConfig) -> FactoryModel:
            output: str | Exception = ModelRequestError("down") if config.model == "primary" else "fallback ok"
            return FactoryModel(output, calls, config.model)

        config = AgentConfig(
            model="primary",
            model_circuit_failure_threshold=1,
            model_circuit_cooldown=60,
            model_fallbacks=(ModelRoute(provider="ollama", model="fallback", base_url="http://fallback"),),
        )
        gateway = ModelGateway(config, ledger=UsageLedger(), model_factory=factory)

        self.assertEqual(gateway.complete([Message(role="user", content="hello")]), "fallback ok")
        self.assertEqual(gateway.complete([Message(role="user", content="hello")]), "fallback ok")

        self.assertEqual(calls, ["primary", "fallback", "fallback"])


if __name__ == "__main__":
    unittest.main()
