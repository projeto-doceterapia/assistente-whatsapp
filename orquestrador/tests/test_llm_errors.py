"""Classification of provider failures and invalid output.

The Gemini SDK is installed only in the image, so the mapper is exercised with
synthetic exceptions that mimic the SDK's shape (an `APIError`-like object with a
`code` int) and with real `httpx` transport/timeout errors. No network, no SDK.
"""

from __future__ import annotations

import httpx
import pytest

from app.llm.errors import classify_provider_error
from app.llm.port import LlmProviderError


class FakeApiError(Exception):
    """Mimics google.genai.errors.APIError: carries the HTTP status on `code`."""

    def __init__(self, code: int) -> None:
        super().__init__(f"api error {code}")
        self.code = code


def test_timeout_is_transient() -> None:
    err = classify_provider_error(httpx.ReadTimeout("slow"))
    assert isinstance(err, LlmProviderError)
    assert err.transient is True


def test_connection_error_is_transient() -> None:
    err = classify_provider_error(httpx.ConnectError("refused"))
    assert err.transient is True


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_rate_limit_and_5xx_are_transient(code: int) -> None:
    assert classify_provider_error(FakeApiError(code)).transient is True


@pytest.mark.parametrize("code", [400, 401, 403])
def test_client_errors_are_not_transient(code: int) -> None:
    err = classify_provider_error(FakeApiError(code))
    assert err.transient is False


def test_unknown_exception_defaults_to_non_transient() -> None:
    err = classify_provider_error(RuntimeError("mystery"))
    assert isinstance(err, LlmProviderError)
    assert err.transient is False
