from __future__ import annotations

import pytest


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENEFICIARIA_TELEFONE", "5511999999999")
    monkeypatch.setenv("CORE_BASE_URL", "http://core:8080")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    for optional in ("CHANNEL", "CONV_TTL_MIN", "MAX_AJUSTES", "TZ"):
        monkeypatch.delenv(optional, raising=False)
