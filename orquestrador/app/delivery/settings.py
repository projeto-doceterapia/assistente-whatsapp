from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise InvalidDeliverySettingError(name, raw) from exc


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class InvalidDeliverySettingError(RuntimeError):
    def __init__(self, name: str, value: str) -> None:
        super().__init__(f"invalid value for setting {name}")
        self.name = name


@dataclass(frozen=True)
class DeliverySettings:
    """Delivery worker tuning. Every value has a safe default so the worker
    starts without any delivery-specific variable set."""

    # How often the agenda is polled, in seconds.
    poll_interval_s: int = 30
    # How far ahead of now the agenda range extends, in minutes.
    agenda_horizon_min: int = 60
    # How far back the agenda range reaches, so windows that opened while the
    # worker was down are still returned for late delivery, in minutes.
    agenda_lookback_min: int = 60
    # Confirmation retry backoff: base seconds, exponential, capped.
    confirm_base_s: int = 2
    confirm_cap_s: int = 60
    confirm_max_tentativas: int = 6
    # How long a send claim without a channel message id is tolerated before it
    # is treated as 'send outcome unknown' on recovery, in seconds.
    claim_ttl_s: int = 120

    @staticmethod
    def from_env() -> "DeliverySettings":
        return DeliverySettings(
            poll_interval_s=_get_int("DELIVERY_POLL_INTERVAL_S", 30),
            agenda_horizon_min=_get_int("DELIVERY_AGENDA_HORIZON_MIN", 60),
            agenda_lookback_min=_get_int("DELIVERY_AGENDA_LOOKBACK_MIN", 60),
            confirm_base_s=_get_int("DELIVERY_CONFIRM_BASE_S", 2),
            confirm_cap_s=_get_int("DELIVERY_CONFIRM_CAP_S", 60),
            confirm_max_tentativas=_get_int("DELIVERY_CONFIRM_MAX_TENTATIVAS", 6),
            claim_ttl_s=_get_int("DELIVERY_CLAIM_TTL_S", 120),
        )
