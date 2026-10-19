"""Types for item-by-item recording.

Each item is recorded in isolation, so each carries its own terminal or pending
status. The status is what lives in `flow:{fluxo_id}:items`, and the result is
what the final message is built from.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ItemStatus(str, Enum):
    """Terminal or in-flight status of one item.

    OK          -> recorded (new or duplicate)
    RETRYABLE   -> failed transiently or because the supply is missing/inactive;
                   eligible for the retry worker or a retry request
    FAILED      -> failed definitively (validation/conflict); never retried
    """

    OK = "OK"
    RETRYABLE = "RETRYABLE"
    FAILED = "FAILED"


class FailureKind(str, Enum):
    """The kind of a retryable failure, so the retry worker knows on-request
    eligibility: a missing/inactive supply is eligible even before the
    automatic attempts are exhausted.

    TRANSIENT       -> timeout, 5xx, network: the core was unavailable.
    SUPPLY_MISSING  -> 404: the supply was missing or inactive.
    """

    TRANSIENT = "TRANSIENT"
    SUPPLY_MISSING = "SUPPLY_MISSING"


class ItemResult(BaseModel):
    """Outcome of recording one draft item.

    `idempotency_key` is the key used, kept so a retry reuses it exactly.
    `motivo` is the PT-BR reason shown to the owner on failure. `falha` is the
    retryable-failure kind, set only when `status` is RETRYABLE.
    """

    index: int
    nome: str
    calculo: str | None = None
    status: ItemStatus
    insumo_id: int | None = None
    idempotency_key: str
    tentativas: int = 1
    motivo: str | None = None
    falha: FailureKind | None = None

    @property
    def ok(self) -> bool:
        return self.status is ItemStatus.OK

    @property
    def retryable(self) -> bool:
        return self.status is ItemStatus.RETRYABLE


class RecordingResult(BaseModel):
    """Aggregate of every item's outcome, used to build the final message."""

    itens: list[ItemResult]

    @property
    def recorded(self) -> list[ItemResult]:
        return [item for item in self.itens if item.ok]

    @property
    def failed(self) -> list[ItemResult]:
        return [item for item in self.itens if not item.ok]

    @property
    def all_ok(self) -> bool:
        return bool(self.itens) and all(item.ok for item in self.itens)

    @property
    def none_ok(self) -> bool:
        return bool(self.itens) and not any(item.ok for item in self.itens)

    @property
    def has_retryable(self) -> bool:
        return any(item.retryable for item in self.failed)
