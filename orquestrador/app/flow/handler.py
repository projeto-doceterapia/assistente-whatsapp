"""Application entry point for one inbound message.

`InboundHandler` is deliberately small: it dedups, serializes per conversation,
loads the state, turns the message into a state-machine event, runs the pure
`transition`, and carries out the resulting actions by delegating to the steps
(extraction, confirmation, adjustment) and the recorder. All business rules
live in the machine and the steps; the handler only wires one message through.
"""

from __future__ import annotations

import uuid

from app.channel.port import InboundMessage, MessageKind
from app.conversation import (
    ConversationContext,
    Event,
    EventKind,
    State,
    transition,
)
from app.conversation.types import (
    AdjustmentLimit,
    AskFinishOrCancel,
    DiscardDraft,
    FreezeAndQueue,
    InformNothingPending,
    ReapplyDraft,
    RemindPending,
    SendReadingAck,
)
from app.flow.adjustment import AdjustmentStep
from app.flow.confirmation import ConfirmationStep
from app.flow.extraction import ExtractionStep, UnsupportedPurchaseError
from app.flow.intent import ReplyIntent
from app.flow.messages import cancel_confirmed
from app.flow.models import ConfirmationOutcome, Draft, FlowDeps
from app.messages import (
    new_purchase_while_pending,
    nothing_pending,
    reading_ack,
    recording_wait,
    third_adjustment,
    unsupported_type,
)
from app.recording import Recorder, final_message, no_op_retry_sink
from app.retry import RetryCoordinator, RetryRequestResult

_FIELD_ESTADO = "estado"
_FIELD_DRAFT = "draft"

# A text reply while awaiting confirmation maps to a machine event by intent. A
# standalone "try again" there is out of context (the retry flow runs in NO_FLOW).
_INTENT_TO_EVENT = {
    ReplyIntent.CONFIRM: EventKind.CONFIRM,
    ReplyIntent.ADJUST: EventKind.ADJUST,
    ReplyIntent.CANCEL: EventKind.CANCEL,
    ReplyIntent.NEW_PURCHASE: EventKind.NEW_PURCHASE,
    ReplyIntent.RETRY: EventKind.OUT_OF_CONTEXT,
    ReplyIntent.OTHER: EventKind.OUT_OF_CONTEXT,
}


class InboundHandler:
    """Runs one inbound message end to end."""

    def __init__(
        self, deps: FlowDeps, retry_coordinator: RetryCoordinator | None = None
    ) -> None:
        self._deps = deps
        self._extraction = ExtractionStep(deps)
        self._confirmation = ConfirmationStep(deps)
        self._adjustment = AdjustmentStep(deps)
        self._retry = retry_coordinator
        sink = retry_coordinator.enqueue if retry_coordinator else no_op_retry_sink
        self._recorder = Recorder(deps.state, deps.core, sink)

    async def handle(self, message: InboundMessage) -> None:
        if message.sender != self._deps.beneficiaria:
            # Only the authorized chat is processed; no content is stored/logged.
            return
        if not await self._deps.state.mark_inbound(message.id):
            return  # already processed (duplicate delivery)
        if not await self._deps.state.acquire_lock(message.sender):
            await self._reply(message.sender, recording_wait())
            return
        try:
            await self._process(message)
        finally:
            await self._deps.state.release_lock(message.sender)

    async def _process(self, message: InboundMessage) -> None:
        estado, draft = await self._load(message.sender)
        if estado is State.NO_FLOW and await self._handle_retry_offer(message):
            return
        ctx = ConversationContext(
            adjustments_done=draft.ajustes if draft else 0,
            max_adjustments=self._deps.max_ajustes,
        )
        event = await self._event_for(estado, message)
        result = transition(estado, event, ctx)
        for action in result.actions:
            await self._run_action(action, message, draft)

    # --- event mapping -----------------------------------------------------

    async def _event_for(self, estado: State, message: InboundMessage) -> Event:
        if estado is State.AWAITING_CONFIRMATION:
            return await self._awaiting_event(message)
        if estado is State.RECORDING:
            return Event(EventKind.ANY_MESSAGE)
        # NO_FLOW and the terminal ENDED (expired then fresh) start a purchase.
        return Event(EventKind.NEW_PURCHASE)

    async def _awaiting_event(self, message: InboundMessage) -> Event:
        if message.kind is MessageKind.IMAGE:
            return Event(EventKind.NEW_PURCHASE)
        if message.kind is not MessageKind.TEXT or not message.text:
            return Event(EventKind.OUT_OF_CONTEXT)
        intent = await self._deps.classifier.classify(message.text)
        return Event(_INTENT_TO_EVENT[intent])

    async def _handle_retry_offer(self, message: InboundMessage) -> bool:
        """When a retry offer is live for this sender, a text
        'tenta de novo' requeues the failed items; any other message drops the
        offer and falls through to normal handling. Returns True when it fully
        answered the message. Only an active/expired offer triggers a classify
        call, so the normal new-purchase path is unchanged."""
        if self._retry is None:
            return False
        status = await self._retry.request_status(message.sender)
        if status == "NONE":
            return False
        if message.kind is not MessageKind.TEXT or not message.text:
            await self._retry.drop_request(message.sender)
            return False
        intent = await self._deps.classifier.classify(message.text)
        if intent is not ReplyIntent.RETRY:
            # A new purchase (or anything else) drops the offer and proceeds.
            await self._retry.drop_request(message.sender)
            return False
        outcome = await self._retry.request_retry(message.sender)
        if outcome.result is RetryRequestResult.NONE:
            return False
        if outcome.message is not None:
            await self._reply(message.sender, outcome.message)
        return True

    # --- action execution --------------------------------------------------

    async def _run_action(
        self, action, message: InboundMessage, draft: Draft | None
    ) -> None:
        sender = message.sender
        if isinstance(action, SendReadingAck):
            await self._start_purchase(message)
        elif isinstance(action, InformNothingPending):
            await self._reply(sender, nothing_pending())
        elif isinstance(action, AskFinishOrCancel):
            await self._reply(sender, new_purchase_while_pending())
        elif isinstance(action, RemindPending):
            await self._remind_pending(sender, draft)
        elif isinstance(action, ReapplyDraft):
            await self._apply_adjustment(message, draft, action.adjustments_done)
        elif isinstance(action, DiscardDraft):
            await self._cancel(sender)
        elif isinstance(action, AdjustmentLimit):
            await self._reply(sender, third_adjustment())
            await self._deps.state.clear_conversation(sender)
        elif isinstance(action, FreezeAndQueue):
            await self._record(sender, draft)
        else:  # StayRecording
            await self._reply(sender, recording_wait())

    async def _start_purchase(self, message: InboundMessage) -> None:
        sender = message.sender
        try:
            categorias = await self._deps.categorias()
            draft = await self._extraction.extract(message, categorias, _new_fluxo_id())
        except UnsupportedPurchaseError:
            await self._reply(sender, unsupported_type())
            await self._deps.state.clear_conversation(sender)
            return

        if not draft.itens:
            # Nothing understood: treat a loose reply as "nothing pending".
            await self._reply(sender, nothing_pending())
            await self._deps.state.clear_conversation(sender)
            return

        await self._reply(sender, reading_ack())
        outcome = await self._confirmation.build(draft)
        await self._finish_confirmation(sender, outcome)

    async def _apply_adjustment(
        self, message: InboundMessage, draft: Draft | None, adjustments_done: int
    ) -> None:
        sender = message.sender
        if draft is None or not message.text:
            await self._reply(sender, recording_wait())
            return
        revisado = await self._adjustment.apply(draft, message.text)
        revisado = revisado.model_copy(update={"ajustes": adjustments_done})
        outcome = await self._confirmation.build(revisado)
        await self._finish_confirmation(sender, outcome)

    async def _remind_pending(self, sender: str, draft: Draft | None) -> None:
        if draft is None:
            await self._reply(sender, nothing_pending())
            return
        # Re-present the pending draft so the owner sees what is awaiting her.
        outcome = await self._confirmation.build(draft)
        await self._reply(sender, outcome.message)

    async def _cancel(self, sender: str) -> None:
        await self._deps.state.clear_conversation(sender)
        await self._reply(sender, cancel_confirmed())

    async def _record(self, sender: str, draft: Draft | None) -> None:
        if draft is None:
            await self._reply(sender, recording_wait())
            return
        await self._reply(sender, recording_wait())
        result = await self._recorder.record(draft)
        # With the retry worker wired, retryable items are enqueued and the final
        # message is sent by the worker when the last one resolves (task 10.2).
        # Without it, finalize returns the message to send right away.
        final = (
            await self._retry.finalize(draft, result)
            if self._retry is not None
            else final_message(result)
        )
        if final is not None:
            await self._reply(sender, final)
            await self._deps.state.clear_conversation(sender)
            return
        # Deferred: items are still being retried. Hold the conversation in
        # RECORDING so a stray message gets the "still recording" reply; the
        # worker clears it when it sends the final result.
        await self._save(sender, State.RECORDING, draft)

    async def _finish_confirmation(
        self, sender: str, outcome: ConfirmationOutcome
    ) -> None:
        await self._reply(sender, outcome.message)
        if outcome.ended or outcome.draft is None:
            await self._deps.state.clear_conversation(sender)
            return
        await self._save(sender, State.AWAITING_CONFIRMATION, outcome.draft)

    # --- persistence helpers ----------------------------------------------

    async def _load(self, sender: str) -> tuple[State, Draft | None]:
        hash_ = await self._deps.state.load_conversation(sender)
        if not hash_:
            return State.NO_FLOW, None
        estado = State(str(hash_.get(_FIELD_ESTADO, State.NO_FLOW.value)))
        draft_raw = hash_.get(_FIELD_DRAFT)
        draft = Draft.model_validate(draft_raw) if draft_raw else None
        return estado, draft

    async def _save(self, sender: str, estado: State, draft: Draft) -> None:
        await self._deps.state.save_conversation(
            sender,
            {_FIELD_ESTADO: estado.value, _FIELD_DRAFT: draft.model_dump(mode="json")},
        )

    async def _reply(self, sender: str, text: str) -> None:
        await self._deps.channel.send_text(sender, text)


def _new_fluxo_id() -> str:
    return uuid.uuid4().hex
