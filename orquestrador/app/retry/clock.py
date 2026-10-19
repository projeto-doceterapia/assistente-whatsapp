from __future__ import annotations

import random
import time
from typing import Protocol


class Clock(Protocol):
    """Injectable monotonic-ish wall clock as epoch seconds, so the scheduler is
    deterministic in tests. Epoch seconds are the retry sorted-set score."""

    def time(self) -> float:
        """Return the current time as epoch seconds (UTC)."""
        ...


class RandomSource(Protocol):
    """Injectable uniform source, so the full-jitter delay is deterministic in
    tests."""

    def uniform(self, low: float, high: float) -> float:
        ...


class SystemClock:
    def time(self) -> float:
        return time.time()


class SystemRandom:
    def __init__(self) -> None:
        self._random = random.SystemRandom()

    def uniform(self, low: float, high: float) -> float:
        return self._random.uniform(low, high)
