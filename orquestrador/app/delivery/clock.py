from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Injectable wall clock, so the scheduler is deterministic in tests."""

    def now(self) -> datetime:
        """Return the current timezone-aware instant (UTC)."""
        ...


class RandomSource(Protocol):
    """Injectable uniform source, so the chosen send time is deterministic in
    tests."""

    def uniform(self, low: float, high: float) -> float:
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)


class SystemRandom:
    def __init__(self) -> None:
        self._random = random.SystemRandom()

    def uniform(self, low: float, high: float) -> float:
        return self._random.uniform(low, high)
