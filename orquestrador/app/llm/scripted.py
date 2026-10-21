"""Env-selected scripted LLM provider for offline runs (no real provider).

`LLM_PROVIDER=scripted` selects a provider that replays recorded JSON responses
loaded from the file named by `FAKE_LLM_FIXTURES`, so the orchestrator runs end
to end without a Gemini key and without the network. It is not used by the
Gemini production path; it exists so the containerized stack can be driven
deterministically in integration and e2e tests.

The fixtures file is a JSON object keyed by schema name. Each schema maps a
lookup key to a recorded response that already conforms to that schema:

    {
      "ExtracaoResultado": {
        "<exact request text>": { ...ExtracaoResultado JSON... },
        "__default__": { "itens": [] }
      },
      "IntencaoResultado": {
        "<exact request text>": { "intencao": "CONFIRMAR" },
        "__default__": { "intencao": "OUTRO" }
      }
    }

The request text is the lookup key, so the caller's message fully determines the
reply. A `__default__` entry is the fallback when no exact match is found; when
it is absent too, the provider raises, surfacing a missing fixture instead of
guessing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.llm.port import LlmConfigError, LlmRequest, LlmResponseError, Schema

_DEFAULT_KEY = "__default__"


class ScriptedLlmProvider:
    """LlmPort that replays recorded responses keyed by schema and request text."""

    def __init__(self, fixtures: dict[str, dict[str, object]]) -> None:
        self._fixtures = fixtures
        self.requests: list[LlmRequest] = []

    @staticmethod
    def from_env() -> "ScriptedLlmProvider":
        path = (os.environ.get("FAKE_LLM_FIXTURES") or "").strip()
        if not path:
            raise LlmConfigError("missing required setting: FAKE_LLM_FIXTURES")
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise LlmConfigError(f"cannot read FAKE_LLM_FIXTURES: {exc}") from exc
        return ScriptedLlmProvider(json.loads(raw))

    async def generate(self, request: LlmRequest, schema: type[Schema]) -> Schema:
        self.requests.append(request)
        by_schema = self._fixtures.get(schema.__name__, {})
        key = (request.text or "").strip()
        payload = by_schema.get(key, by_schema.get(_DEFAULT_KEY))
        if payload is None:
            raise LlmResponseError(
                f"no scripted response for {schema.__name__} text={key!r}"
            )
        return schema.model_validate(payload)
