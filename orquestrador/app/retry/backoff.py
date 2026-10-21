from __future__ import annotations

from app.retry.clock import RandomSource
from app.retry.settings import RetrySettings


def full_jitter_delay(
    attempt: int, settings: RetrySettings, random_source: RandomSource
) -> float:
    """Full-jitter exponential backoff delay in seconds.

    `attempt` is the attempt that just failed, counted from 1. The deterministic
    backoff ceiling is `base * factor ** (attempt - 1)`, capped at `cap_s`; the
    actual delay is a uniform draw in `[0, ceiling]` (full jitter, AWS
    "Exponential Backoff And Jitter"). A fixed `random_source` makes the schedule
    reproducible in tests.
    """
    exponent = max(attempt - 1, 0)
    ceiling = min(settings.base_s * (settings.factor**exponent), settings.cap_s)
    return random_source.uniform(0.0, float(ceiling))
