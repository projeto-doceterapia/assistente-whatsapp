"""Startup wiring teardown: when the full stack cannot be assembled, the
resources already opened (channel, core HTTP client, Redis state) must be closed
and the app must fall back to health-only (`_build_wiring` returns None)."""

from __future__ import annotations

import pytest

from app import main
from app.llm.port import LlmConfigError


class _RecordingResource:
    """Stands in for a closeable resource (channel, http client, Redis state),
    appending its label to a shared list when aclose is awaited."""

    def __init__(self, label: str, closed: list[str]) -> None:
        self._label = label
        self._closed = closed

    async def aclose(self) -> None:
        self._closed.append(self._label)


def test_build_wiring_closes_partial_resources_on_failure(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []
    channel = _RecordingResource("channel", closed)
    http = _RecordingResource("http", closed)
    state = _RecordingResource("state", closed)

    monkeypatch.setattr(main, "_build_channel", lambda: channel)
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: http)
    monkeypatch.setattr(
        main.ConversationState, "from_url", classmethod(lambda cls, *a, **k: state)
    )

    def _boom(*_args: object, **_kwargs: object):
        # Simulate the handler build failing on a missing/invalid setting.
        raise LlmConfigError("missing LLM configuration")

    monkeypatch.setattr(main, "_assemble", _boom)

    result = main._build_wiring()

    assert result is None
    # Closed in reverse order of opening: state, then http, then channel.
    assert closed == ["state", "http", "channel"]


def test_build_wiring_does_not_close_on_success(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []
    channel = _RecordingResource("channel", closed)
    http = _RecordingResource("http", closed)
    state = _RecordingResource("state", closed)

    monkeypatch.setattr(main, "_build_channel", lambda: channel)
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: http)
    monkeypatch.setattr(
        main.ConversationState, "from_url", classmethod(lambda cls, *a, **k: state)
    )
    sentinel = object()
    monkeypatch.setattr(main, "_assemble", lambda *a, **k: sentinel)

    result = main._build_wiring()

    assert result is sentinel
    assert closed == []
