"""Typed vocabulary of the purchase conversation state machine.

This module only declares data. It holds no logic, no I/O and no Redis access,
so the transition function stays pure and fully table-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    """The four conversation states.

    The Portuguese domain names map to:
      NO_FLOW               -> Sem fluxo
      AWAITING_CONFIRMATION -> Aguardando confirmação
      RECORDING             -> Gravando
      ENDED                 -> Encerrado
    """

    NO_FLOW = "NO_FLOW"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    RECORDING = "RECORDING"
    ENDED = "ENDED"


class EventKind(str, Enum):
    """Intents and system signals that drive a transition.

    NEW_PURCHASE    -> Nova compra (imagem/texto)
    LOOSE_REPLY     -> Resposta solta ("sim") with no active flow
    CONFIRM         -> Confirma
    ADJUST          -> Ajusta (free-text correction)
    CANCEL          -> Cancela
    OUT_OF_CONTEXT  -> Fora de contexto (stray message with a pending flow)
    ANY_MESSAGE     -> Qualquer mensagem while recording
    ITEMS_TERMINAL  -> Todos os itens em estado terminal
    TTL_EXPIRED     -> TTL expirado (applies to any state)
    """

    NEW_PURCHASE = "NEW_PURCHASE"
    LOOSE_REPLY = "LOOSE_REPLY"
    CONFIRM = "CONFIRM"
    ADJUST = "ADJUST"
    CANCEL = "CANCEL"
    OUT_OF_CONTEXT = "OUT_OF_CONTEXT"
    ANY_MESSAGE = "ANY_MESSAGE"
    ITEMS_TERMINAL = "ITEMS_TERMINAL"
    TTL_EXPIRED = "TTL_EXPIRED"


@dataclass(frozen=True)
class ConversationContext:
    """Immutable snapshot the machine reads to decide a transition.

    It carries only what the table needs; it is rebuilt by the caller from the
    Redis conversation hash before each `transition` call.

    adjustments_done: how many free-text adjustments were already applied.
    max_adjustments: configured limit on free-text adjustments (default 2).
    """

    adjustments_done: int = 0
    max_adjustments: int = 2


@dataclass(frozen=True)
class Event:
    """A single input to the machine: an intent or a system signal."""

    kind: EventKind


# --- Actions: typed side-effect requests the caller must carry out ---------
#
# The machine never performs these; it only names them. Each action is a
# distinct type so the caller pattern-matches instead of reading a dict.


@dataclass(frozen=True)
class SendReadingAck:
    """Reply that the purchase is being read and start extraction."""


@dataclass(frozen=True)
class InformNothingPending:
    """Tell the owner there is nothing pending."""


@dataclass(frozen=True)
class FreezeAndQueue:
    """Freeze items, create flow:*:items and enqueue recording."""


@dataclass(frozen=True)
class ReapplyDraft:
    """Apply the free-text adjustment and present the draft again."""

    adjustments_done: int


@dataclass(frozen=True)
class DiscardDraft:
    """Discard the current draft (on cancel or on the refused adjustment)."""


@dataclass(frozen=True)
class AdjustmentLimit:
    """Draft discarded because the adjustment limit was exceeded; ask for a
    clearer photo or text."""


@dataclass(frozen=True)
class AskFinishOrCancel:
    """A new purchase arrived mid-flow; ask to finish or cancel the current one."""


@dataclass(frozen=True)
class RemindPending:
    """Remind the owner what is still pending (stray message during a flow)."""


@dataclass(frozen=True)
class StayRecording:
    """Any message during recording: tell the owner to wait."""


@dataclass(frozen=True)
class SendFinalResult:
    """All items reached a terminal status; send the final result."""


Action = (
    SendReadingAck
    | InformNothingPending
    | FreezeAndQueue
    | ReapplyDraft
    | DiscardDraft
    | AdjustmentLimit
    | AskFinishOrCancel
    | RemindPending
    | StayRecording
    | SendFinalResult
)


@dataclass(frozen=True)
class TransitionResult:
    """What `transition` returns: the next state and the ordered actions."""

    next_state: State
    actions: tuple[Action, ...]
