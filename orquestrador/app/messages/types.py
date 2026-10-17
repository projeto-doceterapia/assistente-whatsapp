"""Typed inputs for the PT-BR message catalog.

Rendering takes these dataclasses, never a loose dict, so every field a
message needs is named and typed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ItemMarker(str, Enum):
    """How an item is shown in the confirmation.

    CONFIRM -> known supply, will be updated (shown with ✔)
    NEW     -> not in the catalog, needs approval (shown with ⚠)
    CLARIFY -> ambiguous, blocks recording until clarified (shown with ❓)
    """

    CONFIRM = "CONFIRM"
    NEW = "NEW"
    CLARIFY = "CLARIFY"


@dataclass(frozen=True)
class ConfirmationItem:
    """One line of the confirmation summary.

    nome: supply name (or the proposed new name).
    marker: how to flag the line.
    calculo: the full calculation to show, e.g. "2 × 5 kg = 10 kg". Optional
        for items shown without a conversion (a plain quantity).
    categoria: proposed category, only meaningful when marker is NEW.
    duvida: the clarification question, only meaningful when marker is CLARIFY.
    """

    nome: str
    marker: ItemMarker
    calculo: str | None = None
    categoria: str | None = None
    duvida: str | None = None


@dataclass(frozen=True)
class RecordedItem:
    """An item that was recorded, for the final result summary."""

    nome: str
    calculo: str | None = None


@dataclass(frozen=True)
class FailedItem:
    """An item that could not be recorded, for the final result summary.

    retryable: whether the owner can ask to try it again.
    """

    nome: str
    motivo: str
    retryable: bool = True
