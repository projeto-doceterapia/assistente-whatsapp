"""Table-driven tests for the conversation state machine.

Every row of the transition table is one case here, plus TTL expiry from each
state, the refused third adjustment, cancel, new purchase during the flow and
a loose reply with no flow.
"""

from __future__ import annotations

import pytest

from app.conversation import transition
from app.conversation.machine import InvalidTransition
from app.conversation.types import (
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
)

# (id, state, event_kind, context, expected_next_state, expected_action_types)
_ROWS = [
    (
        "no-flow:new-purchase",
        State.NO_FLOW,
        EventKind.NEW_PURCHASE,
        None,
        State.AWAITING_CONFIRMATION,
        (SendReadingAck,),
    ),
    (
        "no-flow:loose-reply",
        State.NO_FLOW,
        EventKind.LOOSE_REPLY,
        None,
        State.NO_FLOW,
        (InformNothingPending,),
    ),
    (
        "awaiting:confirm",
        State.AWAITING_CONFIRMATION,
        EventKind.CONFIRM,
        None,
        State.RECORDING,
        (FreezeAndQueue,),
    ),
    (
        "awaiting:adjust-first",
        State.AWAITING_CONFIRMATION,
        EventKind.ADJUST,
        ConversationContext(adjustments_done=0, max_adjustments=2),
        State.AWAITING_CONFIRMATION,
        (ReapplyDraft,),
    ),
    (
        "awaiting:adjust-second",
        State.AWAITING_CONFIRMATION,
        EventKind.ADJUST,
        ConversationContext(adjustments_done=1, max_adjustments=2),
        State.AWAITING_CONFIRMATION,
        (ReapplyDraft,),
    ),
    (
        "awaiting:adjust-third-refused",
        State.AWAITING_CONFIRMATION,
        EventKind.ADJUST,
        ConversationContext(adjustments_done=2, max_adjustments=2),
        State.ENDED,
        (AdjustmentLimit,),
    ),
    (
        "awaiting:cancel",
        State.AWAITING_CONFIRMATION,
        EventKind.CANCEL,
        None,
        State.ENDED,
        (DiscardDraft,),
    ),
    (
        "awaiting:new-purchase",
        State.AWAITING_CONFIRMATION,
        EventKind.NEW_PURCHASE,
        None,
        State.AWAITING_CONFIRMATION,
        (AskFinishOrCancel,),
    ),
    (
        "awaiting:out-of-context",
        State.AWAITING_CONFIRMATION,
        EventKind.OUT_OF_CONTEXT,
        None,
        State.AWAITING_CONFIRMATION,
        (RemindPending,),
    ),
    (
        "recording:any-message",
        State.RECORDING,
        EventKind.ANY_MESSAGE,
        None,
        State.RECORDING,
        (StayRecording,),
    ),
    (
        "recording:items-terminal",
        State.RECORDING,
        EventKind.ITEMS_TERMINAL,
        None,
        State.ENDED,
        (SendFinalResult,),
    ),
]


@pytest.mark.parametrize(
    "state, event_kind, context, expected_state, expected_actions",
    [(r[1], r[2], r[3], r[4], r[5]) for r in _ROWS],
    ids=[r[0] for r in _ROWS],
)
def test_transition_table_rows(state, event_kind, context, expected_state, expected_actions):
    result = transition(state, Event(event_kind), context)

    assert result.next_state is expected_state
    assert tuple(type(a) for a in result.actions) == expected_actions


@pytest.mark.parametrize(
    "state",
    [State.NO_FLOW, State.AWAITING_CONFIRMATION, State.RECORDING, State.ENDED],
)
def test_ttl_expired_from_any_state_resets(state):
    # "Qualquer | TTL expirado": the state disappears and the next message
    # starts a new flow, with no action attached.
    result = transition(state, Event(EventKind.TTL_EXPIRED))

    assert result.next_state is State.NO_FLOW
    assert result.actions == ()


def test_third_adjustment_discards_draft_and_ends():
    result = transition(
        State.AWAITING_CONFIRMATION,
        Event(EventKind.ADJUST),
        ConversationContext(adjustments_done=2, max_adjustments=2),
    )

    assert result.next_state is State.ENDED
    assert [type(a) for a in result.actions] == [AdjustmentLimit]


def test_adjustment_counts_the_new_attempt():
    result = transition(
        State.AWAITING_CONFIRMATION,
        Event(EventKind.ADJUST),
        ConversationContext(adjustments_done=1, max_adjustments=2),
    )

    action = result.actions[0]
    assert isinstance(action, ReapplyDraft)
    assert action.adjustments_done == 2


def test_cancel_discards_and_ends():
    result = transition(State.AWAITING_CONFIRMATION, Event(EventKind.CANCEL))

    assert result.next_state is State.ENDED
    assert [type(a) for a in result.actions] == [DiscardDraft]


def test_new_purchase_during_flow_is_refused():
    result = transition(State.AWAITING_CONFIRMATION, Event(EventKind.NEW_PURCHASE))

    assert result.next_state is State.AWAITING_CONFIRMATION
    assert [type(a) for a in result.actions] == [AskFinishOrCancel]


def test_loose_reply_with_no_flow():
    result = transition(State.NO_FLOW, Event(EventKind.LOOSE_REPLY))

    assert result.next_state is State.NO_FLOW
    assert [type(a) for a in result.actions] == [InformNothingPending]


def test_custom_limit_allows_more_adjustments():
    result = transition(
        State.AWAITING_CONFIRMATION,
        Event(EventKind.ADJUST),
        ConversationContext(adjustments_done=2, max_adjustments=3),
    )

    assert result.next_state is State.AWAITING_CONFIRMATION
    assert isinstance(result.actions[0], ReapplyDraft)


@pytest.mark.parametrize(
    "state, event_kind",
    [
        (State.NO_FLOW, EventKind.CONFIRM),
        (State.NO_FLOW, EventKind.ADJUST),
        (State.AWAITING_CONFIRMATION, EventKind.LOOSE_REPLY),
        (State.RECORDING, EventKind.CONFIRM),
        (State.ENDED, EventKind.CONFIRM),
    ],
)
def test_undefined_rows_raise(state, event_kind):
    with pytest.raises(InvalidTransition):
        transition(state, Event(event_kind))


def test_transition_is_pure_no_mutation_of_context():
    ctx = ConversationContext(adjustments_done=1, max_adjustments=2)
    transition(State.AWAITING_CONFIRMATION, Event(EventKind.ADJUST), ctx)

    assert ctx.adjustments_done == 1
    assert ctx.max_adjustments == 2
