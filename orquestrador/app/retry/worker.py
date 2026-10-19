"""Persistent per-item retry worker.

A single asyncio task polls the Redis retry sorted set and, for each item whose
next-attempt time has arrived, replays its recording with the SAME idempotency
key. Everything it needs is in Redis, so it survives a process kill: on restart a
fresh worker over the same Redis resumes the schedule. A per-item NX claim makes
processing single-flight, so two workers never resend the same item. On a
definitive error or once the configured attempts are exhausted, the item goes to
the DLQ and is marked eligible for an on-request retry. When the last retryable
item of a purchase resolves, the worker sends the final result message (summary
first) and opens the on-request retry window.

A `Clock` and a `RandomSource` are injected so the backoff schedule is
deterministic in tests.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.channel.port import ChannelPort
from app.flow.models import Draft
from app.flow.ports import StatePort
from app.recording.models import ItemStatus, RecordingResult
from app.retry.backoff import full_jitter_delay
from app.retry.clock import Clock, RandomSource
from app.retry.coordinator import RetryCoordinator
from app.retry.models import RetryItem, RetryReason
from app.retry.settings import RetrySettings
from app.retry.state import RetryState

logger = logging.getLogger("retry")

# Replays one single-item draft through the recorder, reusing its key.
Replayer = Callable[[Draft], Awaitable[RecordingResult]]


class AlertRaiser(Protocol):
    """Port for an operational alert when an item exhausts its retries: items
    that could not be recorded are raised as an operational alert. The
    retry worker hands the whole DLQ item so the alert can derive a stable,
    non-sensitive dedup suffix; it never puts item content in the alert text."""

    async def raise_dlq_item(self, item: object) -> None: ...


class RetryWorker:
    """Drains the retry queue, one claimed item at a time."""

    def __init__(
        self,
        *,
        state: RetryState,
        conversation: StatePort,
        channel: ChannelPort,
        replay: Replayer,
        coordinator: RetryCoordinator,
        settings: RetrySettings,
        clock: Clock,
        random_source: RandomSource,
        alerts: AlertRaiser | None = None,
    ) -> None:
        self._state = state
        self._conversation = conversation
        self._channel = channel
        self._replay = replay
        self._coordinator = coordinator
        self._settings = settings
        self._clock = clock
        self._random = random_source
        self._alerts = alerts
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="retry-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:  # one bad poll must not kill the loop
                logger.exception("retry_poll_failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.poll_interval_s
                )
            except asyncio.TimeoutError:
                continue

    async def poll_once(self) -> None:
        """Process every item due now. Public so tests drive it without the loop."""
        now = self._clock.time()
        for member in await self._state.due_members(now):
            try:
                await self._process(member)
            except Exception:
                logger.exception("retry_item_failed", extra={"member": member})

    async def _process(self, member: str) -> None:
        # Single-flight: only the worker that flips the NX claim handles the item.
        if not await self._state.claim(member):
            return
        item = await self._state.load(member)
        if item is None:
            await self._state.unschedule(member)
            await self._state.release_claim(member)
            return
        try:
            result = await self._replay(item.draft)
        except Exception:
            # The replay blew up before a verdict: leave the schedule entry so a
            # later poll retries, and free the claim.
            await self._state.release_claim(member)
            raise

        outcome = result.itens[0] if result.itens else None
        if outcome is not None and outcome.ok:
            await self._succeed(item)
        elif outcome is not None and outcome.retryable:
            await self._retry_or_exhaust(item)
        else:
            await self._definitive(item, outcome.motivo if outcome else None)
        await self._state.release_claim(member)

    async def _succeed(self, item: RetryItem) -> None:
        await self._state.unschedule(item.member)
        await self._state.resolve_member(
            item.fluxo_id,
            item.member,
            {"status": ItemStatus.OK.value, "nome": item.nome, "calculo": item.calculo},
        )
        await self._state.forget(item.member)
        await self._complete_if_last(item.fluxo_id)

    async def _retry_or_exhaust(self, item: RetryItem) -> None:
        tentativas = item.tentativas + 1
        if tentativas >= self._settings.max_tentativas:
            await self._exhaust(item.model_copy(update={"tentativas": tentativas}))
            return
        updated = item.model_copy(update={"tentativas": tentativas})
        delay = full_jitter_delay(tentativas, self._settings, self._random)
        await self._state.schedule(updated, self._clock.time() + delay)

    async def _exhaust(self, item: RetryItem) -> None:
        await self._state.unschedule(item.member)
        await self._state.push_dlq(
            {
                "fluxo_id": item.fluxo_id,
                "item_key": item.item_key,
                "nome": item.nome,
                "reason": item.reason.value,
                "tentativas": item.tentativas,
            }
        )
        # Exhausted (or supply missing/inactive) items are eligible for the
        # owner's on-request retry, so the record is kept, not forgotten.
        await self._state.resolve_member(
            item.fluxo_id,
            item.member,
            {
                "status": ItemStatus.RETRYABLE.value,
                "nome": item.nome,
                "motivo": _exhausted_motivo(item),
                "eligible": True,
            },
        )
        if self._alerts is not None:
            # The whole item is passed so the alert derives its dedup suffix; its
            # content never reaches the owner-facing alert text (ops owns that).
            await self._alerts.raise_dlq_item(item)
        await self._complete_if_last(item.fluxo_id)

    async def _definitive(self, item: RetryItem, motivo: str | None) -> None:
        await self._state.unschedule(item.member)
        await self._state.resolve_member(
            item.fluxo_id,
            item.member,
            {
                "status": ItemStatus.FAILED.value,
                "nome": item.nome,
                "motivo": motivo or "não passou na validação",
                "eligible": False,
            },
        )
        await self._state.forget(item.member)
        await self._complete_if_last(item.fluxo_id)

    async def _complete_if_last(self, fluxo_id: str) -> None:
        # Only the single caller that drives the counter to exactly zero composes
        # the final message. A remaining count or the ALREADY_COMPLETED sentinel
        # (a crashed or duplicated completion) is a no-op, so the final message
        # is never sent twice.
        if await self._state.decrement_pending(fluxo_id) != 0:
            return
        composed = await self._coordinator.compose_final(fluxo_id)
        if composed is None:
            return
        sender, message, eligible = composed
        await self._channel.send_text(sender, message)
        await self._conversation.clear_conversation(sender)
        await self._state.open_request_window(
            sender, eligible, self._settings.request_window_s, self._clock.time()
        )
        await self._state.drop_final(fluxo_id)


def _exhausted_motivo(item: RetryItem) -> str:
    if item.reason is RetryReason.SUPPLY_MISSING:
        return "o insumo segue indisponível"
    return "o sistema seguiu indisponível"
