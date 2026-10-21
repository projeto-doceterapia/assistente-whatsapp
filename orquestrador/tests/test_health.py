from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config import MissingSettingError, Settings
from app.main import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_settings_read_from_env(base_env: None) -> None:
    settings = Settings.from_env()
    assert settings.channel == "baileys"
    assert settings.beneficiaria_telefone == "5511999999999"
    assert settings.core_base_url == "http://core:8080"
    assert settings.conv_ttl_min == 30
    assert settings.max_ajustes == 2
    assert settings.retry.max_tentativas == 6
    assert settings.retry.cap_s == 300
    assert settings.tz == "America/Sao_Paulo"


def test_settings_missing_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENEFICIARIA_TELEFONE", raising=False)
    monkeypatch.setenv("CORE_BASE_URL", "http://core:8080")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with pytest.raises(MissingSettingError):
        Settings.from_env()
