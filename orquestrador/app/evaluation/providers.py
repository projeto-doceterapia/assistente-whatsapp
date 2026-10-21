from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from app.llm.fake import FakeLlmProvider
from app.llm.port import GeminiSettings, LlmConfigError, LlmPort


@runtime_checkable
class ReportaUso(Protocol):
    """Optional capability: a provider that records token usage per call.

    ``usos`` holds one dict per :meth:`LlmPort.generate` call, with at least
    ``prompt_tokens``, ``output_tokens`` and ``total_tokens`` when the backend
    returns them. The runner reads it when present and reports usage/cost as
    unavailable otherwise, so the fake provider (which records nothing) and a
    future Gemini build that does report usage are both handled without changing
    the port.
    """

    usos: list[dict[str, int]]


class ProviderError(RuntimeError):
    """Raised when the requested provider cannot be built."""


def construir_provider(nome: str) -> LlmPort:
    """Build the LLM provider the evaluation runs against.

    ``fake`` is only for tests and offline replays and must be seeded per case by
    the caller, so it is not built here. ``gemini`` is built from the environment
    and needs ``GEMINI_API_KEY`` and ``GEMINI_MODEL``; without them the runner
    reports the missing requirement rather than calling a real provider.
    """
    if nome == "fake":
        raise ProviderError(
            "the fake provider must be seeded per case; pass recorded responses"
        )
    if nome == "gemini":
        from app.llm.gemini import GeminiProvider  # noqa: PLC0415 - optional SDK import

        try:
            settings = GeminiSettings.from_env()
        except LlmConfigError as exc:
            raise ProviderError(str(exc)) from exc
        return GeminiProvider(settings)
    raise ProviderError(f"unknown provider: {nome}")


def provider_de_env() -> str:
    """Resolve the provider name from ``LLM_PROVIDER`` (default ``gemini``)."""
    return (os.environ.get("LLM_PROVIDER") or "gemini").strip()


def fake_para_resposta(resposta_json: str) -> FakeLlmProvider:
    """Build a single-shot fake provider that replays one recorded response."""
    return FakeLlmProvider([resposta_json])
