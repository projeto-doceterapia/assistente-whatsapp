"""Settings for the operational layer.

Every value has a safe default so the layer starts without any ops-specific
variable set. These are read by `main` when wiring the ops services; the ops
package never reads `app/config.py` so its configuration stays local.
"""

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
        raise InvalidOpsSettingError(name, raw) from exc


class InvalidOpsSettingError(RuntimeError):
    def __init__(self, name: str, value: str) -> None:
        super().__init__(f"invalid value for setting {name}")
        self.name = name


@dataclass(frozen=True)
class OpsSettings:
    """Operational-alert tuning.

    `alert_cooldown_s` is the per-key dedup window that prevents alert storms:
    the same alert key is not re-raised until it expires (default 30 min).
    `core_failure_threshold` is how many consecutive core failures the health
    monitor tolerates before raising a core-down alert.
    `categorias_ttl_s` is the lifetime of the cached supply categories.
    `health_poll_interval_s` is how often the channel/Redis health monitor runs.
    """

    alert_cooldown_s: int = 30 * 60
    core_failure_threshold: int = 3
    ai_failure_threshold: int = 3
    categorias_ttl_s: int = 300
    health_poll_interval_s: int = 60

    @staticmethod
    def from_env() -> "OpsSettings":
        return OpsSettings(
            alert_cooldown_s=_get_int("OPS_ALERT_COOLDOWN_S", 30 * 60),
            core_failure_threshold=_get_int("OPS_CORE_FAILURE_THRESHOLD", 3),
            ai_failure_threshold=_get_int("OPS_AI_FAILURE_THRESHOLD", 3),
            categorias_ttl_s=_get_int("OPS_CATEGORIAS_TTL_S", 300),
            health_poll_interval_s=_get_int("OPS_HEALTH_POLL_INTERVAL_S", 60),
        )
