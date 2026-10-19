from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

Schema = TypeVar("Schema", bound=BaseModel)


class LlmError(RuntimeError):
    """Base error for LLM calls."""


class LlmConfigError(LlmError):
    """A required LLM setting is missing or invalid."""


class LlmResponseError(LlmError):
    """The provider returned a body that does not validate against the schema."""


class LlmInvalidOutputError(LlmError):
    """The provider replied, but the body violates the schema.

    This is an EXTRACTION-side failure, not a provider outage: the model was
    reachable and answered, the answer is just unusable. The flow asks for a
    clearer photo or a typed message and raises no operational alert.
    """


class LlmProviderError(LlmError):
    """The provider call itself failed: timeout, network, 5xx, 429, auth, quota.

    ``transient`` tells the extraction path whether a brief retry is worth it:
    timeouts, connection errors, HTTP 429 and 5xx are transient; 400/401/403 and
    a permanently exhausted quota are not. A provider error that outlives the
    bounded retry is the only signal that feeds the AI-error alert.
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class ImageInput:
    """A raw image to send to the model, with its MIME type."""

    data: bytes
    mime_type: str = "image/jpeg"


@dataclass(frozen=True)
class LlmRequest:
    """One structured-output call.

    ``system`` carries the versioned PT-BR prompt (catalog, categories and unit
    table already rendered in). ``text`` is the user message, and ``image`` the
    optional receipt photo. The provider must return JSON validated against the
    pydantic schema passed to :meth:`LlmPort.generate`.
    """

    system: str
    text: str | None = None
    image: ImageInput | None = None


@runtime_checkable
class LlmPort(Protocol):
    """The port every LLM provider (Gemini, fake) implements identically.

    It is async, takes text and/or image input, and returns an instance of the
    requested pydantic schema. Code validates the parsed object again before use.
    """

    async def generate(self, request: LlmRequest, schema: type[Schema]) -> Schema:
        """Run one structured-output generation and return a validated schema."""
        ...


@dataclass(frozen=True)
class GeminiSettings:
    """Provider settings read from the environment.

    ``LLM_PROVIDER`` selects the provider (``gemini`` here); ``GEMINI_API_KEY``
    and ``GEMINI_MODEL`` configure the call. No value is baked into the image.
    """

    api_key: str
    model: str
    provider: str = "gemini"

    @staticmethod
    def from_env() -> "GeminiSettings":
        provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip()
        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        model = (os.environ.get("GEMINI_MODEL") or "").strip()
        if not api_key:
            raise LlmConfigError("missing required setting: GEMINI_API_KEY")
        if not model:
            raise LlmConfigError("missing required setting: GEMINI_MODEL")
        return GeminiSettings(api_key=api_key, model=model, provider=provider)
