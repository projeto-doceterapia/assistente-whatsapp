"""Access key decoding for NFC-e receipts.

Decodes the 44-digit NF-e/NFC-e access key from a receipt image (NFC-e QR code
or DANFE Code 128 / ITF barcode) and validates it with the modulo-11 check
digit. A key read by the AI is accepted only through the same validation.
"""

from __future__ import annotations

from app.invoice.decoder import DecodableImage, decode_image
from app.invoice.keys import (
    extract_key_from_barcode_text,
    extract_key_from_qr_payload,
    is_valid_access_key,
    resolve_access_key,
)
from app.invoice.models import (
    AccessKeyOutcome,
    AccessKeySource,
    DecodedCode,
    DecodeOutcome,
)

__all__ = [
    "AccessKeyOutcome",
    "AccessKeySource",
    "DecodableImage",
    "DecodeOutcome",
    "DecodedCode",
    "decode_image",
    "extract_key_from_barcode_text",
    "extract_key_from_qr_payload",
    "is_valid_access_key",
    "resolve_access_key",
]
