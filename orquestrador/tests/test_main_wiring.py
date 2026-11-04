"""Startup wiring teardown: when the full stack cannot be assembled, the
resources already opened (channel, core HTTP client, Redis state) must be closed
and the app must fall back to health-only (`_build_wiring` returns None)."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

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
    monkeypatch.setattr(main, "criar_http_core", lambda *_a, **_k: http)
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
    monkeypatch.setattr(main, "criar_http_core", lambda *_a, **_k: http)
    monkeypatch.setattr(
        main.ConversationState, "from_url", classmethod(lambda cls, *a, **k: state)
    )
    sentinel = object()
    monkeypatch.setattr(main, "_assemble", lambda *a, **k: sentinel)

    result = main._build_wiring()

    assert result is sentinel
    assert closed == []


def _wiring_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.getMessage() == "wiring_disabled_missing_setting"
    ]


def test_missing_token_falls_back_to_health_only(
    base_env: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("ORQUESTRADOR_TOKEN")
    # configure_logging replaces the root handlers, which would drop caplog's.
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    caplog.set_level(logging.WARNING, logger="app.main")

    app = main.create_app()

    assert TestClient(app).get("/health").status_code == 200
    assert not [route.path for route in app.routes if "webhook" in route.path]
    registros = _wiring_warnings(caplog)
    assert len(registros) == 1
    assert registros[0].levelno == logging.WARNING
    assert registros[0].setting == "ORQUESTRADOR_TOKEN"


def test_missing_channel_setting_is_logged_by_name(
    base_env: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("ADAPTER_URL", raising=False)
    caplog.set_level(logging.WARNING, logger="app.main")

    assert main._build_wiring() is None

    assert [record.setting for record in _wiring_warnings(caplog)] == ["ADAPTER_URL"]
