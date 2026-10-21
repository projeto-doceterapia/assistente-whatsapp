"""Env-selected LLM provider factory.

Production uses Gemini; `LLM_PROVIDER=scripted` selects the offline scripted
provider so the stack runs end to end without a real key or the network. The
factory is the single place `main.py` builds the provider from, so the wiring
does not hardcode a provider.
"""

from __future__ import annotations

import os

from app.llm.gemini import GeminiProvider
from app.llm.port import GeminiSettings, LlmPort
from app.llm.scripted import ScriptedLlmProvider


def build_llm_provider() -> LlmPort:
    provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip()
    if provider == "scripted":
        return ScriptedLlmProvider.from_env()
    return GeminiProvider(GeminiSettings.from_env())
