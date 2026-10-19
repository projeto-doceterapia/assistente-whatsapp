"""Durable retry records held in Redis.

A retry record carries everything needed to resend one item with the SAME
idempotency key, without any in-memory state: a single-item copy of the draft
(so the worker replays the exact recording path, which rebuilds the deterministic
idempotency key), the sender the final message goes to, and the display fields for that
message. It survives a process kill because it lives only in Redis.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from app.flow.models import Draft


class RetryReason(str, Enum):
    """Why an item is retryable, which decides on-request eligibility.

    TRANSIENT       -> the core was down/flaky (timeout, 5xx); eligible for an
                       on-request retry only once automatic attempts are
                       exhausted (it is in the DLQ).
    SUPPLY_MISSING  -> the supply was missing or inactive (404); eligible for an
                       on-request retry even before attempts are exhausted.
    """

    TRANSIENT = "TRANSIENT"
    SUPPLY_MISSING = "SUPPLY_MISSING"


class RetryItem(BaseModel):
    """One item queued for automatic and on-request retry.

    `tentativas` counts the attempts already made (the recording was attempt 1).
    `draft` holds only this item, so the worker replays it through the recorder
    and the deterministic idempotency key is reused exactly.
    """

    fluxo_id: str
    item_key: str
    index: int
    sender: str
    nome: str
    calculo: str | None = None
    draft: Draft
    reason: RetryReason
    tentativas: int = 1
    exhausted: bool = False

    @property
    def member(self) -> str:
        return f"{self.fluxo_id}:{self.item_key}"

    @property
    def request_eligible(self) -> bool:
        """Whether an on-request retry may replay this item (automatic
        retries exhausted, OR the supply was missing/inactive)."""
        return self.exhausted or self.reason is RetryReason.SUPPLY_MISSING
