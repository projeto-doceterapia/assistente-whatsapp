"""Persistent retry subsystem.

`RetryCoordinator` is the recorder's `RetrySink` and owns final-result
composition and the on-request 'try again' flow. `RetryWorker` is the background
task that drains the Redis retry queue with full-jitter exponential backoff,
single-flight per item, a DLQ on exhaustion, and the final result message when a
purchase's last retry resolves. All state is in Redis, so both survive a restart.
"""

from __future__ import annotations

from app.channel.port import ChannelPort
from app.flow.ports import StatePort
from app.retry.clock import Clock, RandomSource, SystemClock, SystemRandom
from app.retry.coordinator import (
    RetryCoordinator,
    RetryRequestOutcome,
    RetryRequestResult,
)
from app.retry.models import RetryItem, RetryReason
from app.retry.settings import RetrySettings
from app.retry.state import RetryState
from app.retry.worker import AlertRaiser, Replayer, RetryWorker

__all__ = [
    "AlertRaiser",
    "Clock",
    "RandomSource",
    "Replayer",
    "RetryCoordinator",
    "RetryItem",
    "RetryReason",
    "RetryRequestOutcome",
    "RetryRequestResult",
    "RetrySettings",
    "RetryState",
    "RetryWorker",
    "SystemClock",
    "SystemRandom",
    "create_retry_coordinator",
    "create_retry_worker",
]


def create_retry_coordinator(
    *,
    redis: object,
    recipient: str,
    settings: RetrySettings | None = None,
    clock: Clock | None = None,
    random_source: RandomSource | None = None,
) -> RetryCoordinator:
    """Factory with explicit dependencies, so `main` and the handler wire the
    coordinator without the retry package importing application wiring."""
    return RetryCoordinator(
        state=RetryState(redis),
        recipient=recipient,
        settings=settings or RetrySettings.from_env(),
        clock=clock or SystemClock(),
        random_source=random_source or SystemRandom(),
    )


def create_retry_worker(
    *,
    redis: object,
    conversation: StatePort,
    channel: ChannelPort,
    replay: Replayer,
    coordinator: RetryCoordinator,
    settings: RetrySettings | None = None,
    clock: Clock | None = None,
    random_source: RandomSource | None = None,
    alerts: AlertRaiser | None = None,
) -> RetryWorker:
    return RetryWorker(
        state=RetryState(redis),
        conversation=conversation,
        channel=channel,
        replay=replay,
        coordinator=coordinator,
        settings=settings or RetrySettings.from_env(),
        clock=clock or SystemClock(),
        random_source=random_source or SystemRandom(),
        alerts=alerts,
    )
