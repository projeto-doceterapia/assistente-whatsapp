from __future__ import annotations

import os
from dataclasses import dataclass


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is not None:
        value = value.strip()
    return value or default


def _get_required(name: str) -> str:
    value = _get(name)
    if not value:
        raise MissingSettingError(name)
    return value


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidSettingError(name, raw) from exc


class MissingSettingError(RuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(f"missing required setting: {name}")
        self.name = name


class InvalidSettingError(RuntimeError):
    def __init__(self, name: str, value: str) -> None:
        super().__init__(f"invalid value for setting {name}")
        self.name = name


@dataclass(frozen=True)
class RetryPolicy:
    max_tentativas: int
    base_s: int
    cap_s: int


@dataclass(frozen=True)
class Settings:
    channel: str
    beneficiaria_telefone: str
    core_base_url: str
    redis_url: str
    conv_ttl_min: int
    max_ajustes: int
    retry: RetryPolicy
    tz: str
    # Optional integrations are read lazily by the components that need them,
    # so that /health and the Redis/core layers start without channel or LLM
    # credentials present.
    adapter_url: str | None = None
    llm_provider: str | None = None

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            channel=_get("CHANNEL", "baileys"),
            beneficiaria_telefone=_get_required("BENEFICIARIA_TELEFONE"),
            core_base_url=_get_required("CORE_BASE_URL"),
            redis_url=_get_required("REDIS_URL"),
            conv_ttl_min=_get_int("CONV_TTL_MIN", 30),
            max_ajustes=_get_int("MAX_AJUSTES", 2),
            retry=RetryPolicy(
                max_tentativas=_get_int("RETRY_MAX_TENTATIVAS", 6),
                base_s=_get_int("RETRY_BASE_S", 2),
                cap_s=_get_int("RETRY_CAP_S", 300),
            ),
            tz=_get("TZ", "America/Sao_Paulo"),
            adapter_url=_get("ADAPTER_URL"),
            llm_provider=_get("LLM_PROVIDER"),
        )
