"""Bounded transient retry around the LLM port.

A transient provider error is replayed up to the attempt limit; a non-transient
one and any other error propagate on the first raise. The sleep is injected, so
the schedule is asserted without real delays.
"""

from __future__ import annotations

import pytest

from app.llm.port import LlmProviderError, LlmRequest
from app.llm.retry import RetrySettings, RetryingLlmProvider, retry_transient

pytestmark = pytest.mark.asyncio


class FlakyProvider:
    """Raises a scripted sequence of errors, then returns a sentinel value."""

    def __init__(self, errors: list[Exception]) -> None:
        self._errors = list(errors)
        self.calls = 0

    async def generate(self, request: LlmRequest, schema: type):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return "ok"


class SleepSpy:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


async def test_transient_error_is_retried_then_succeeds() -> None:
    provider = FlakyProvider([LlmProviderError("timeout", transient=True)])
    sleep = SleepSpy()
    wrapped = RetryingLlmProvider(
        provider, RetrySettings(max_tentativas=3, backoff_base_s=1.0), sleep
    )

    out = await wrapped.generate(LlmRequest(system="s"), object)

    assert out == "ok"
    assert provider.calls == 2  # first failed, retry succeeded
    assert sleep.waits == [1.0]  # one backoff of base * 2**0


async def test_exponential_backoff_schedule() -> None:
    provider = FlakyProvider(
        [LlmProviderError("t", transient=True), LlmProviderError("t", transient=True)]
    )
    sleep = SleepSpy()
    wrapped = RetryingLlmProvider(
        provider, RetrySettings(max_tentativas=3, backoff_base_s=2.0), sleep
    )

    await wrapped.generate(LlmRequest(system="s"), object)

    assert provider.calls == 3
    assert sleep.waits == [2.0, 4.0]  # base*2**0, base*2**1


async def test_exhausted_transient_retries_raise() -> None:
    provider = FlakyProvider(
        [LlmProviderError("t", transient=True)] * 5
    )
    sleep = SleepSpy()
    wrapped = RetryingLlmProvider(
        provider, RetrySettings(max_tentativas=3, backoff_base_s=1.0), sleep
    )

    with pytest.raises(LlmProviderError):
        await wrapped.generate(LlmRequest(system="s"), object)

    assert provider.calls == 3  # exactly the attempt limit
    assert len(sleep.waits) == 2  # one wait between each of the 3 attempts


async def test_non_transient_error_is_not_retried() -> None:
    provider = FlakyProvider([LlmProviderError("auth", transient=False)])
    sleep = SleepSpy()
    wrapped = RetryingLlmProvider(
        provider, RetrySettings(max_tentativas=3, backoff_base_s=1.0), sleep
    )

    with pytest.raises(LlmProviderError):
        await wrapped.generate(LlmRequest(system="s"), object)

    assert provider.calls == 1
    assert sleep.waits == []


async def test_retry_transient_helper_wraps_any_coroutine() -> None:
    attempts = {"n": 0}

    async def call() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise LlmProviderError("t", transient=True)
        return "done"

    sleep = SleepSpy()
    out = await retry_transient(
        call, RetrySettings(max_tentativas=2, backoff_base_s=0.5), sleep
    )

    assert out == "done"
    assert attempts["n"] == 2
    assert sleep.waits == [0.5]


async def test_settings_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MAX_TENTATIVAS", raising=False)
    monkeypatch.delenv("LLM_BACKOFF_BASE_S", raising=False)
    cfg = RetrySettings.from_env()
    assert cfg.max_tentativas == 3
    assert cfg.backoff_base_s == 1.0


async def test_settings_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MAX_TENTATIVAS", "5")
    monkeypatch.setenv("LLM_BACKOFF_BASE_S", "0.25")
    cfg = RetrySettings.from_env()
    assert cfg.max_tentativas == 5
    assert cfg.backoff_base_s == 0.25
