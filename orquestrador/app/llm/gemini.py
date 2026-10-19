from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.llm.errors import classify_provider_error
from app.llm.port import (
    GeminiSettings,
    ImageInput,
    LlmConfigError,
    LlmInvalidOutputError,
    LlmRequest,
    Schema,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, SDK is optional at runtime
    pass


class GeminiProvider:
    """LlmPort backed by the official Google GenAI SDK (`google-genai`).

    The SDK is imported lazily so that tests (which use the fake provider) never
    need it installed and never hit the network. The async client runs one
    structured-output call per :meth:`generate`, with the schema handed to the
    model so Gemini returns JSON that already conforms to it; code validates it
    again before returning.
    """

    def __init__(self, settings: GeminiSettings) -> None:
        self._settings = settings
        self._client = None

    def _ensure_client(self) -> object:
        if self._client is not None:
            return self._client
        try:
            from google import genai  # noqa: PLC0415 - optional runtime dependency
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK
            raise LlmConfigError(
                "google-genai is not installed; cannot use the Gemini provider"
            ) from exc
        self._client = genai.Client(api_key=self._settings.api_key)
        return self._client

    @staticmethod
    def _build_contents(request: LlmRequest, parts_factory: object) -> list[object]:
        # parts_factory is google.genai.types.Part; passed in to keep the import local.
        parts: list[object] = []
        if request.text:
            parts.append(parts_factory.from_text(text=request.text))
        if request.image is not None:
            image: ImageInput = request.image
            parts.append(
                parts_factory.from_bytes(data=image.data, mime_type=image.mime_type)
            )
        return parts

    async def generate(self, request: LlmRequest, schema: type[Schema]) -> Schema:
        from google.genai import types  # noqa: PLC0415 - optional runtime dependency

        client = self._ensure_client()
        contents = self._build_contents(request, types.Part)
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            response_mime_type="application/json",
            response_schema=schema,
        )
        try:
            response = await client.aio.models.generate_content(  # type: ignore[attr-defined]
                model=self._settings.model,
                contents=contents,
                config=config,
            )
        except LlmConfigError:
            raise
        except Exception as exc:  # SDK/httpx failure -> typed provider error.
            raise classify_provider_error(exc) from exc
        raw = getattr(response, "text", None)
        if not raw:
            # The provider answered, but with nothing usable: an extraction-side
            # failure, not an outage, so it never feeds the AI-error alert.
            raise LlmInvalidOutputError("Gemini returned an empty body")
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            raise LlmInvalidOutputError("Gemini body does not match the schema") from exc
