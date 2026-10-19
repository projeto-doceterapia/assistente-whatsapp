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
        raise InvalidRetrySettingError(name, raw) from exc


class InvalidRetrySettingError(RuntimeError):
    def __init__(self, name: str, value: str) -> None:
        super().__init__(f"invalid value for setting {name}")
        self.name = name


@dataclass(frozen=True)
class RetrySettings:
    """Retry worker tuning.

    The backoff policy is full-jitter exponential backoff with a
    base, a growth factor and a cap, bounded by a maximum number of attempts.
    `request_window_s` is the window during which the owner can ask to retry the
    failed items (default 24 h). Every value has a safe default so the
    worker starts with no retry-specific variable set.
    """

    # How often the retry queue is polled, in seconds.
    poll_interval_s: int = 5
    # Backoff base seconds, growth factor and cap (defaults: 2 s / 2 / 300 s).
    base_s: int = 2
    factor: int = 2
    cap_s: int = 300
    # Attempts per item before it goes to the DLQ (default 6).
    max_tentativas: int = 6
    # On-request retry window, in seconds (default 24 h).
    request_window_s: int = 24 * 3600

    @staticmethod
    def from_env() -> "RetrySettings":
        return RetrySettings(
            poll_interval_s=_get_int("RETRY_POLL_INTERVAL_S", 5),
            base_s=_get_int("RETRY_BASE_S", 2),
            factor=_get_int("RETRY_FACTOR", 2),
            cap_s=_get_int("RETRY_CAP_S", 300),
            max_tentativas=_get_int("RETRY_MAX_TENTATIVAS", 6),
            request_window_s=_get_int("RETRY_REQUEST_WINDOW_H", 24) * 3600,
        )
