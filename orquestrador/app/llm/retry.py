"""Bounded transient retry around any `LlmPort`.

A transient provider failure (timeout, network, 429, 5xx) is worth a brief
retry; a non-transient one (400/401/403, permanent quota) and an invalid-output
failure are not. This decorator wraps a provider and replays only transient
`LlmProviderError`s, with exponential backoff, up to a bounded number of
attempts. Everything else propagates unchanged on the first raise.

The sleep is injected so tests drive the schedule without real delays, and the
limits come from the environment (`LLM_MAX_TENTATIVAS`, `LLM_BACKOFF_BASE_S`) so
the provider stays configuration-free.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from app.llm.port import LlmPort, LlmProviderError, LlmRequest, Schema

Sleeper = Callable[[float], Awaitable[None]]
_T = TypeVar("_T")


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class RetrySettings:
    """Transient-retry limits for the extraction path.

    ``max_tentativas`` is the total number of attempts (so 3 means one call plus
    two retries). ``backoff_base_s`` is the first delay; each further wait grows
    geometrically (base * 2 ** (attempt - 1)).
    """

    max_tentativas: int = 3
    backoff_base_s: float = 1.0

    @staticmethod
    def from_env() -> "RetrySettings":
        return RetrySettings(
            max_tentativas=max(1, _get_int("LLM_MAX_TENTATIVAS", 3)),
            backoff_base_s=_get_float("LLM_BACKOFF_BASE_S", 1.0),
        )


class RetryingLlmProvider:
    """Wrap an `LlmPort`, retrying only transient provider errors."""

    def __init__(
        self,
        inner: LlmPort,
        settings: RetrySettings | None = None,
        sleep: Sleeper | None = None,
    ) -> None:
        self._inner = inner
        self._settings = settings or RetrySettings()
        self._sleep = sleep or asyncio.sleep

    async def generate(self, request: LlmRequest, schema: type[Schema]) -> Schema:
        return await retry_transient(
            lambda: self._inner.generate(request, schema),
            self._settings,
            self._sleep,
        )


async def retry_transient(
    call: Callable[[], Awaitable["_T"]],
    settings: RetrySettings | None = None,
    sleep: Sleeper | None = None,
) -> "_T":
    """Run ``call`` again on a transient `LlmProviderError`, with backoff.

    Only transient provider errors are retried; a non-transient provider error,
    an invalid-output error and any other exception propagate on the first
    raise. Shared by the provider decorator and the extraction step so both
    honour the same bounded policy with an injected sleep.
    """
    cfg = settings or RetrySettings()
    wait = sleep or asyncio.sleep
    ultimo: LlmProviderError | None = None
    for tentativa in range(1, cfg.max_tentativas + 1):
        try:
            return await call()
        except LlmProviderError as exc:
            if not exc.transient or tentativa >= cfg.max_tentativas:
                raise
            ultimo = exc
            await wait(cfg.backoff_base_s * 2 ** (tentativa - 1))
    raise ultimo or LlmProviderError("llm retry exhausted", transient=True)
