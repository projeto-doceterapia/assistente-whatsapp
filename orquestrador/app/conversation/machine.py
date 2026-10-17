"""Pure transition function for the purchase conversation.

`transition` is side-effect free: given the current state, an event and the
conversation context, it returns the next state plus the actions the caller
must perform. It never reads or writes Redis and never calls the channel.
"""

from __future__ import annotations

from app.conversation.types import (
    Action,
    AdjustmentLimit,
    AskFinishOrCancel,
    ConversationContext,
    DiscardDraft,
    Event,
    EventKind,
    FreezeAndQueue,
    InformNothingPending,
    ReapplyDraft,
    RemindPending,
    SendFinalResult,
    SendReadingAck,
    StayRecording,
    State,
    TransitionResult,
)


class InvalidTransition(ValueError):
    """Raised when an event cannot be handled in the current state.

    Every state/event combination is covered, so this only fires on a caller bug
    that builds an event the table does not define for the state.
    """

    def __init__(self, state: State, event: Event) -> None:
        super().__init__(f"no transition for {event.kind.value} in {state.value}")
        self.state = state
        self.event = event


def transition(
    state: State, event: Event, context: ConversationContext | None = None
) -> TransitionResult:
    """Return the next state and ordered actions for `event` in `state`.

    `context` defaults to the standard limits; it is only read for the
    adjustment-count branch (Ajusta 1º/2º vs. 3º).
    """
    ctx = context or ConversationContext()

    # "Qualquer | TTL expirado": the state disappears and the next message
    # starts fresh. Handled first because it applies to every state.
    if event.kind is EventKind.TTL_EXPIRED:
        return _result(State.NO_FLOW)

    if state is State.NO_FLOW:
        return _from_no_flow(state, event)
    if state is State.AWAITING_CONFIRMATION:
        return _from_awaiting(state, event, ctx)
    if state is State.RECORDING:
        return _from_recording(state, event)

    # ENDED is terminal: nothing leaves it except TTL expiry, which
    # was already handled above.
    raise InvalidTransition(state, event)


def _from_no_flow(state: State, event: Event) -> TransitionResult:
    if event.kind is EventKind.NEW_PURCHASE:
        return _result(State.AWAITING_CONFIRMATION, SendReadingAck())
    if event.kind is EventKind.LOOSE_REPLY:
        return _result(State.NO_FLOW, InformNothingPending())
    raise InvalidTransition(state, event)


def _from_awaiting(
    state: State, event: Event, ctx: ConversationContext
) -> TransitionResult:
    if event.kind is EventKind.CONFIRM:
        return _result(State.RECORDING, FreezeAndQueue())
    if event.kind is EventKind.ADJUST:
        return _handle_adjust(ctx)
    if event.kind is EventKind.CANCEL:
        return _result(State.ENDED, DiscardDraft())
    if event.kind is EventKind.NEW_PURCHASE:
        return _result(State.AWAITING_CONFIRMATION, AskFinishOrCancel())
    if event.kind is EventKind.OUT_OF_CONTEXT:
        return _result(State.AWAITING_CONFIRMATION, RemindPending())
    raise InvalidTransition(state, event)


def _handle_adjust(ctx: ConversationContext) -> TransitionResult:
    # The 1st and 2nd adjustments re-present the draft. The one past the limit
    # (the 3rd with the default of 2) discards the draft and ends the flow.
    if ctx.adjustments_done >= ctx.max_adjustments:
        return _result(State.ENDED, AdjustmentLimit())
    return _result(
        State.AWAITING_CONFIRMATION, ReapplyDraft(adjustments_done=ctx.adjustments_done + 1)
    )


def _from_recording(state: State, event: Event) -> TransitionResult:
    if event.kind is EventKind.ITEMS_TERMINAL:
        return _result(State.ENDED, SendFinalResult())
    if event.kind is EventKind.ANY_MESSAGE:
        return _result(State.RECORDING, StayRecording())
    raise InvalidTransition(state, event)


def _result(next_state: State, *actions: Action) -> TransitionResult:
    return TransitionResult(next_state=next_state, actions=tuple(actions))
