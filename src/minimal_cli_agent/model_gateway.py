from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from minimal_cli_agent.exceptions import ConfigurationError, ModelRequestError
from minimal_cli_agent.interfaces import Model
from minimal_cli_agent.model import ChatModel
from minimal_cli_agent.types import AgentConfig, Message, ModelRoute


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(messages: list[Message]) -> int:
    return sum(estimate_tokens(message.content) + 4 for message in messages)


def estimate_cost(input_tokens: int, output_tokens: int, route: ModelRoute) -> float:
    return (input_tokens / 1_000_000 * route.price_input_per_1m) + (
        output_tokens / 1_000_000 * route.price_output_per_1m
    )


@dataclass(frozen=True)
class UsageRecord:
    request_id: str
    timestamp: str
    subject: str
    tenant: str
    provider: str
    model: str
    base_url: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: float
    latency_ms: int
    status: str
    error: str = ""
    fallback_index: int = 0
    attempt: int = 1
    billable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "subject": self.subject,
            "tenant": self.tenant,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "prompt_version": self.prompt_version,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": self.estimated_cost,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "error": self.error,
            "fallback_index": self.fallback_index,
            "attempt": self.attempt,
            "billable": self.billable,
        }


class UsageLedger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.records: list[UsageRecord] = []
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    def append(self, record: UsageRecord) -> None:
        with self._lock:
            self.records.append(record)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    def totals(self, subject: str, tenant: str, period: str) -> tuple[int, float]:
        now = datetime.now(timezone.utc)
        token_total = 0
        cost_total = 0.0
        with self._lock:
            records = list(self.records)
        for record in records:
            if not record.billable or record.subject != subject or record.tenant != tenant:
                continue
            try:
                timestamp = datetime.fromisoformat(record.timestamp)
            except ValueError:
                continue
            if period == "day" and timestamp.date() != now.date():
                continue
            if period == "month" and (timestamp.year, timestamp.month) != (now.year, now.month):
                continue
            token_total += record.total_tokens
            cost_total += record.estimated_cost
        return token_total, cost_total

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                self.records.append(UsageRecord(**data))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue


@dataclass
class CircuitState:
    failures: int = 0
    opened_until: float = 0.0


class CircuitBreaker:
    def __init__(self, failure_threshold: int, cooldown: float) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown = max(0.1, cooldown)
        self._states: dict[str, CircuitState] = {}
        self._lock = threading.Lock()

    def allow(self, route: ModelRoute) -> bool:
        with self._lock:
            state = self._states.get(route.key)
            return state is None or state.opened_until <= time.monotonic()

    def record_success(self, route: ModelRoute) -> None:
        with self._lock:
            self._states[route.key] = CircuitState()

    def record_failure(self, route: ModelRoute) -> None:
        with self._lock:
            state = self._states.setdefault(route.key, CircuitState())
            state.failures += 1
            if state.failures >= self.failure_threshold:
                state.opened_until = time.monotonic() + self.cooldown


class UsageLimiter:
    def __init__(self, config: AgentConfig, ledger: UsageLedger) -> None:
        self.config = config
        self.ledger = ledger

    def check(self, input_tokens: int, route: ModelRoute) -> None:
        output_budget = self.config.max_output_tokens or 0
        total_budget = input_tokens + output_budget
        request_cost = estimate_cost(input_tokens, output_budget, route)

        if self.config.max_input_tokens is not None and input_tokens > self.config.max_input_tokens:
            raise ModelRequestError(f"Model input token limit exceeded: {input_tokens}>{self.config.max_input_tokens}")
        if self.config.max_request_tokens is not None and total_budget > self.config.max_request_tokens:
            raise ModelRequestError(f"Model request token limit exceeded: {total_budget}>{self.config.max_request_tokens}")
        if self.config.max_request_cost is not None and request_cost > self.config.max_request_cost:
            raise ModelRequestError(f"Model request cost limit exceeded: {request_cost:.6f}>{self.config.max_request_cost:.6f}")

        daily_tokens, daily_cost = self.ledger.totals(self.config.usage_subject, self.config.usage_tenant, "day")
        monthly_tokens, monthly_cost = self.ledger.totals(self.config.usage_subject, self.config.usage_tenant, "month")
        if self.config.daily_token_limit is not None and daily_tokens + total_budget > self.config.daily_token_limit:
            raise ModelRequestError("Daily model token limit exceeded.")
        if self.config.monthly_token_limit is not None and monthly_tokens + total_budget > self.config.monthly_token_limit:
            raise ModelRequestError("Monthly model token limit exceeded.")
        if self.config.daily_cost_limit is not None and daily_cost + request_cost > self.config.daily_cost_limit:
            raise ModelRequestError("Daily model cost limit exceeded.")
        if self.config.monthly_cost_limit is not None and monthly_cost + request_cost > self.config.monthly_cost_limit:
            raise ModelRequestError("Monthly model cost limit exceeded.")


class KeyPool:
    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = tuple(key for key in keys if key)
        self._index = 0
        self._lock = threading.Lock()

    def next(self, fallback: str | None) -> str | None:
        if not self.keys:
            return fallback
        with self._lock:
            key = self.keys[self._index % len(self.keys)]
            self._index += 1
            return key


class ModelGateway:
    def __init__(
        self,
        config: AgentConfig,
        ledger: UsageLedger | None = None,
        model_factory: Callable[[AgentConfig], Model] | None = None,
    ) -> None:
        self.config = config
        self.ledger = ledger or UsageLedger(config.usage_ledger_path)
        self.limiter = UsageLimiter(config, self.ledger)
        self.circuit_breaker = CircuitBreaker(config.model_circuit_failure_threshold, config.model_circuit_cooldown)
        self.model_factory = model_factory or ChatModel
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._models: dict[str, Model] = {}
        self._semaphore_lock = threading.Lock()
        self._model_lock = threading.Lock()
        self._key_pool = KeyPool(tuple(key.strip() for key in (config.api_key or "").split(",") if key.strip()))

    def complete(self, messages: list[Message]) -> str:
        input_tokens = estimate_message_tokens(messages)
        routes = self._routes()
        if not routes:
            raise ConfigurationError("No model routes configured.")

        request_id = str(uuid4())
        last_error: Exception | None = None
        for fallback_index, route in enumerate(routes):
            if not self.circuit_breaker.allow(route):
                last_error = ModelRequestError(f"Model route circuit is open: {route.provider}/{route.model}")
                continue
            self.limiter.check(input_tokens, route)
            retries = max(0, route.max_retries)
            for attempt in range(1, retries + 2):
                semaphore = self._semaphore(route)
                acquired = semaphore.acquire(timeout=self.config.model_queue_timeout)
                if not acquired:
                    last_error = ModelRequestError(f"Model route concurrency limit reached: {route.provider}/{route.model}")
                    self.circuit_breaker.record_failure(route)
                    continue
                started = time.monotonic()
                output = ""
                try:
                    output = self._model_for(route).complete(messages)
                    if self.config.validate_non_empty_model_output and not output.strip():
                        raise ModelRequestError("Model returned an empty response.")
                    latency_ms = int((time.monotonic() - started) * 1000)
                    self.circuit_breaker.record_success(route)
                    self._record(
                        request_id=request_id,
                        route=route,
                        input_tokens=input_tokens,
                        output=output,
                        latency_ms=latency_ms,
                        status="success",
                        fallback_index=fallback_index,
                        attempt=attempt,
                        billable=True,
                    )
                    return output
                except ModelRequestError as exc:
                    last_error = exc
                    latency_ms = int((time.monotonic() - started) * 1000)
                    self.circuit_breaker.record_failure(route)
                    self._record(
                        request_id=request_id,
                        route=route,
                        input_tokens=input_tokens,
                        output=output,
                        latency_ms=latency_ms,
                        status="error",
                        error=str(exc),
                        fallback_index=fallback_index,
                        attempt=attempt,
                        billable=self.config.bill_failed_requests,
                    )
                finally:
                    semaphore.release()
        if last_error is not None:
            raise ModelRequestError(f"All model routes failed: {last_error}") from last_error
        raise ModelRequestError("All model routes failed.")

    def _routes(self) -> list[ModelRoute]:
        primary = ModelRoute(
            provider=self.config.provider,
            model=self.config.model,
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.model_timeout,
            max_retries=self.config.model_max_retries,
            price_input_per_1m=self.config.model_price_input_per_1m,
            price_output_per_1m=self.config.model_price_output_per_1m,
        )
        return [primary, *self.config.model_fallbacks]

    def _model_for(self, route: ModelRoute) -> Model:
        api_key = self._key_pool.next(route.api_key)
        cache_key = f"{route.key}:{api_key or ''}:{route.timeout or self.config.model_timeout}"
        with self._model_lock:
            model = self._models.get(cache_key)
            if model is not None:
                return model
        route_config = replace(
            self.config,
            provider=route.provider,
            model=route.model,
            base_url=route.base_url,
            api_key=api_key,
            model_timeout=route.timeout or self.config.model_timeout,
        )
        model = self.model_factory(route_config)
        with self._model_lock:
            return self._models.setdefault(cache_key, model)

    def _semaphore(self, route: ModelRoute) -> threading.BoundedSemaphore:
        with self._semaphore_lock:
            semaphore = self._semaphores.get(route.key)
            if semaphore is None:
                semaphore = threading.BoundedSemaphore(max(1, self.config.model_max_concurrency))
                self._semaphores[route.key] = semaphore
            return semaphore

    def _record(
        self,
        request_id: str,
        route: ModelRoute,
        input_tokens: int,
        output: str,
        latency_ms: int,
        status: str,
        fallback_index: int,
        attempt: int,
        billable: bool,
        error: str = "",
    ) -> None:
        output_tokens = estimate_tokens(output)
        total_tokens = input_tokens + output_tokens
        self.ledger.append(
            UsageRecord(
                request_id=request_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                subject=self.config.usage_subject,
                tenant=self.config.usage_tenant,
                provider=route.provider,
                model=route.model,
                base_url=route.base_url,
                prompt_version=self.config.prompt_version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost=estimate_cost(input_tokens, output_tokens, route),
                latency_ms=latency_ms,
                status=status,
                error=error,
                fallback_index=fallback_index,
                attempt=attempt,
                billable=billable,
            )
        )
