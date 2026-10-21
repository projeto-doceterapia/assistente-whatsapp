"""Bridges recording and the retry worker.

The coordinator is the `RetrySink` the recorder calls for each retryable item:
it persists a durable `RetryItem` and schedules the first automatic retry. After
recording, the handler calls `finalize`, which decides whether the final result
can be sent now (nothing is being retried) or must wait for the worker to drain
the retries. It also reconstructs the final message from the per-item outcomes,
so success/partial/failure wording stays in the message catalog.

The recipient is the single authorized owner (only her chat is processed), so it
is fixed at construction, like the delivery worker's recipient.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.flow.models import Draft
from app.messages import retry_started, retry_window_expired
from app.messages.catalog import final_failure_retryable
from app.messages.types import FailedItem, RecordedItem
from app.recording import RetryEnqueue, final_message
from app.recording.models import ItemResult, ItemStatus, RecordingResult
from app.retry.backoff import full_jitter_delay
from app.retry.clock import Clock, RandomSource
from app.retry.models import RetryItem, RetryReason
from app.retry.settings import RetrySettings
from app.retry.state import RetryState

_REASON_FROM_FAILURE = {
    "TRANSIENT": RetryReason.TRANSIENT,
    "SUPPLY_MISSING": RetryReason.SUPPLY_MISSING,
}


class RetryRequestResult(str, Enum):
    """What happened to an on-request 'try again'.

    STARTED  -> a retry window was active; the eligible items were requeued.
    EXPIRED  -> a window existed but closed; the owner is told it expired.
    NONE     -> nothing was offered; the caller handles it as a new interaction.
    """

    STARTED = "STARTED"
    EXPIRED = "EXPIRED"
    NONE = "NONE"


@dataclass(frozen=True)
class RetryRequestOutcome:
    result: RetryRequestResult
    message: str | None = None


def _item_key(index: int) -> str:
    return f"item:{index}"


def _member(fluxo_id: str, index: int) -> str:
    return f"{fluxo_id}:{_item_key(index)}"


class RetryCoordinator:
    """Owns enqueue-on-failure and final-result composition."""

    def __init__(
        self,
        *,
        state: RetryState,
        recipient: str,
        settings: RetrySettings,
        clock: Clock,
        random_source: RandomSource,
    ) -> None:
        self._state = state
        self._recipient = recipient
        self._settings = settings
        self._clock = clock
        self._random = random_source

    async def enqueue(self, enqueue: RetryEnqueue) -> None:
        """`RetrySink` entry point: schedule the first automatic retry."""
        result = enqueue.result
        reason = _REASON_FROM_FAILURE.get(
            result.falha.value if result.falha else "TRANSIENT", RetryReason.TRANSIENT
        )
        single = enqueue.draft.model_copy(update={"itens": [enqueue.item]})
        item = RetryItem(
            fluxo_id=enqueue.draft.fluxo_id,
            item_key=_item_key(result.index),
            index=result.index,
            sender=self._recipient,
            nome=result.nome,
            calculo=result.calculo,
            draft=single,
            reason=reason,
            tentativas=result.tentativas,
        )
        delay = full_jitter_delay(item.tentativas, self._settings, self._random)
        await self._state.schedule(item, self._clock.time() + delay)

    async def finalize(
        self, draft: Draft, result: RecordingResult
    ) -> str | None:
        """Called once recording returns. When nothing is being retried, return
        the final message for the handler to send now. Otherwise capture the
        snapshot and the pending count and return None: the worker sends the
        final message when the last retry resolves."""
        retrying = [item for item in result.itens if item.retryable]
        if not retrying:
            return final_message(result)

        snapshot = {
            "sender": self._recipient,
            "recorded": [
                {"nome": i.nome, "calculo": i.calculo} for i in result.recorded
            ],
            "failed_definitive": [
                {"nome": i.nome, "motivo": i.motivo or "não foi possível registrar"}
                for i in result.failed
                if not i.retryable
            ],
            "retry_members": [_member(draft.fluxo_id, i.index) for i in retrying],
        }
        await self._state.save_final(draft.fluxo_id, snapshot)
        await self._state.set_pending(draft.fluxo_id, len(retrying))
        return None

    async def request_status(self, sender: str) -> str:
        """'ACTIVE', 'EXPIRED' or 'NONE' for the sender's retry window now."""
        status, _ = await self._state.request_status(sender, self._clock.time())
        return status

    async def request_retry(self, sender: str) -> RetryRequestOutcome:
        """Handle an on-request 'try again'. When a window is active,
        requeue only its eligible items (same keys), reset the pending count and
        snapshot so the worker reports a fresh result, and return STARTED."""
        status, members = await self._state.request_status(sender, self._clock.time())
        if status == "NONE":
            return RetryRequestOutcome(RetryRequestResult.NONE)
        if status == "EXPIRED":
            await self._state.close_request_window(sender)
            return RetryRequestOutcome(
                RetryRequestResult.EXPIRED, retry_window_expired()
            )

        requeued: dict[str, list[str]] = {}
        for member in members:
            item = await self._state.load(member)
            if item is None:
                continue
            fresh = item.model_copy(update={"tentativas": 1})
            await self._state.schedule(fresh, self._clock.time())
            await self._state.drop_resolution(item.fluxo_id, member)
            requeued.setdefault(item.fluxo_id, []).append(member)
        if not requeued:
            await self._state.close_request_window(sender)
            return RetryRequestOutcome(
                RetryRequestResult.EXPIRED, retry_window_expired()
            )
        # Each purchase's items are retried together: one fresh snapshot and
        # pending count per purchase drive one new final message.
        for fluxo_id, members_here in requeued.items():
            await self._state.save_final(
                fluxo_id,
                {
                    "sender": sender,
                    "recorded": [],
                    "failed_definitive": [],
                    "retry_members": members_here,
                },
            )
            await self._state.set_pending(fluxo_id, len(members_here))
        await self._state.close_request_window(sender)
        return RetryRequestOutcome(RetryRequestResult.STARTED, retry_started())

    async def drop_request(self, sender: str) -> None:
        """Drop a pending retry offer (a new purchase arrived)."""
        await self._state.close_request_window(sender)

    async def compose_final(self, fluxo_id: str) -> tuple[str, str, list[str]] | None:
        """Rebuild the final message once every retry resolved. Returns the
        sender, the message text and the members still eligible for an on-request
        retry, or None when there is no snapshot."""
        snapshot = await self._state.load_final(fluxo_id)
        if snapshot is None:
            return None
        sender = str(snapshot["sender"])
        recorded = [
            RecordedItem(nome=r["nome"], calculo=r.get("calculo"))
            for r in snapshot["recorded"]
        ]
        failed = [
            FailedItem(nome=f["nome"], motivo=f["motivo"], retryable=False)
            for f in snapshot["failed_definitive"]
        ]
        eligible: list[str] = []
        for member in snapshot["retry_members"]:
            outcome = await self._state.resolution(fluxo_id, member)
            if outcome is None or outcome.get("status") == ItemStatus.OK.value:
                recorded.append(
                    RecordedItem(
                        nome=(outcome or {}).get("nome", ""),
                        calculo=(outcome or {}).get("calculo"),
                    )
                )
                continue
            failed.append(
                FailedItem(
                    nome=outcome["nome"],
                    motivo=outcome["motivo"],
                    retryable=bool(outcome.get("eligible", False)),
                )
            )
            if outcome.get("eligible"):
                eligible.append(member)
        message = _message_from_parts(recorded, failed)
        return sender, message, eligible


def _message_from_parts(recorded: list[RecordedItem], failed: list[FailedItem]) -> str:
    """Reuse the recording summary wording by rebuilding an equivalent
    `RecordingResult`-shaped message. The catalog owns the actual text."""
    itens: list[ItemResult] = []
    for i, rec in enumerate(recorded):
        itens.append(
            ItemResult(
                index=i,
                nome=rec.nome,
                calculo=rec.calculo,
                status=ItemStatus.OK,
                idempotency_key="",
            )
        )
    for j, fail in enumerate(failed):
        itens.append(
            ItemResult(
                index=len(recorded) + j,
                nome=fail.nome,
                calculo=None,
                status=ItemStatus.RETRYABLE if fail.retryable else ItemStatus.FAILED,
                idempotency_key="",
                motivo=fail.motivo,
            )
        )
    result = RecordingResult(itens=itens)
    # A total failure whose items are still retryable must offer the on-request
    # retry; the plain total-failure wording has no offer.
    if result.none_ok and any(f.retryable for f in failed):
        return final_failure_retryable(failed)
    return final_message(result)
