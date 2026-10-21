"""Map Gemini SDK / httpx exceptions onto the typed LLM errors.

The Gemini SDK (`google-genai`) is installed only in the image, so this module
never imports it at module load time: it inspects a raised exception by its
class name and `code` attribute (``APIError.code`` is the HTTP status) plus the
``httpx`` transport/timeout types, which are always available. That keeps the
mapper unit-testable with synthetic exceptions, with no network and no SDK.

Classification (design: transient failures are worth a brief retry):
  - timeouts, connection/transport errors, HTTP 429 and 5xx -> transient;
  - HTTP 400/401/403 and a permanently exhausted quota -> not transient.

A schema violation is not a provider outage: `GeminiProvider` raises
`LlmInvalidOutputError` for it directly, so it never reaches this mapper.
"""

from __future__ import annotations

import httpx

from app.llm.port import LlmProviderError

# APIError subclasses carry the HTTP status on `.code`; these are transient.
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def classify_provider_error(exc: BaseException) -> LlmProviderError:
    """Turn a raw SDK/httpx exception into a typed `LlmProviderError`.

    Network and timeout errors from httpx are always transient. For an SDK
    `APIError` the HTTP status decides: 429 and 5xx are transient, every other
    4xx (400/401/403, permanent quota) is not. Anything unrecognized is treated
    as non-transient, so an unknown failure is reported as unavailable without
    being retried blindly.
    """
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return LlmProviderError(f"gemini transport error: {exc!r}", transient=True)

    code = _status_code(exc)
    if code is not None:
        transient = code in _TRANSIENT_STATUS or 500 <= code < 600
        return LlmProviderError(f"gemini HTTP {code}", transient=transient)

    return LlmProviderError(f"gemini provider error: {exc!r}", transient=False)
