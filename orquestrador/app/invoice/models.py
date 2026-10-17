from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class AccessKeySource(str, Enum):
    """Where a resolved access key came from."""

    QR_CODE = "QR_CODE"
    BARCODE = "BARCODE"
    AI = "AI"


class DecodedCode(BaseModel):
    """A single machine-readable code found in a receipt image.

    ``text`` is the raw decoded payload; ``is_qr`` tells a QR (NFC-e URL)
    from a linear barcode (DANFE Code 128 / ITF) so key extraction can pick
    the right parser.
    """

    text: str
    is_qr: bool


class DecodeOutcome(BaseModel):
    """Result of decoding an image: every code found, in reading order."""

    codes: list[DecodedCode]


class AccessKeyOutcome(BaseModel):
    """Final access-key decision for a receipt.

    ``key`` is the validated 44-digit key, or ``None`` when the purchase has
    no usable key. ``source`` is set only when a key was resolved.
    """

    key: str | None = None
    source: AccessKeySource | None = None

    @property
    def has_key(self) -> bool:
        return self.key is not None
