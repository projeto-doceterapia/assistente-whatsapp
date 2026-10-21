from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.invoice.models import AccessKeyOutcome, AccessKeySource, DecodedCode

ACCESS_KEY_LENGTH = 44

_ONLY_DIGITS = re.compile(r"\D")
_FORTY_FOUR_DIGITS = re.compile(r"\d{44}")


def _digits(value: str) -> str:
    return _ONLY_DIGITS.sub("", value)


def is_valid_access_key(key: str | None) -> bool:
    """Return True if ``key`` is a 44-digit NF-e key with a valid modulo-11 DV.

    The check digit is the last of the 44 digits, computed over the first 43
    with weights cycling 2..9 from right to left. A remainder-based DV of 0 or
    1 maps to 0, per the NF-e specification.
    """
    if key is None or len(key) != ACCESS_KEY_LENGTH or not key.isdigit():
        return False
    body, expected = key[:-1], int(key[-1])
    weight = 2
    total = 0
    for digit in reversed(body):
        total += int(digit) * weight
        weight = 2 if weight == 9 else weight + 1
    remainder = total % 11
    check_digit = 0 if remainder < 2 else 11 - remainder
    return check_digit == expected


def extract_key_from_qr_payload(payload: str) -> str | None:
    """Extract the access key from an NFC-e QR payload.

    The key is the ``p`` query parameter's first ``|``-separated field, or the
    ``chNFe`` parameter. Falls back to the first bare 44-digit run. The key is
    returned as-is, without validation (the caller validates).
    """
    query = urlsplit(payload.strip()).query or payload.strip()
    params = parse_qs(query, keep_blank_values=True)

    p_values = params.get("p")
    if p_values:
        candidate = _digits(p_values[0].split("|", 1)[0])
        if len(candidate) == ACCESS_KEY_LENGTH:
            return candidate

    ch_values = params.get("chNFe")
    if ch_values:
        candidate = _digits(ch_values[0])
        if len(candidate) == ACCESS_KEY_LENGTH:
            return candidate

    match = _FORTY_FOUR_DIGITS.search(_digits(payload))
    return match.group(0) if match else None


def extract_key_from_barcode_text(text: str) -> str | None:
    """Extract the access key from a DANFE linear barcode payload.

    The barcode encodes the 44 digits directly; any separators are stripped.
    """
    candidate = _digits(text)
    return candidate if len(candidate) == ACCESS_KEY_LENGTH else None


def _key_from_code(code: DecodedCode) -> str | None:
    if code.is_qr:
        return extract_key_from_qr_payload(code.text)
    return extract_key_from_barcode_text(code.text)


def resolve_access_key(
    codes: list[DecodedCode], ai_key: str | None = None
) -> AccessKeyOutcome:
    """Decide the receipt's access key from decoded codes and an optional AI key.

    A decoded code wins over the AI: QR codes are tried first, then
    linear barcodes. Only a key that passes modulo-11 is accepted. If no code
    yields a valid key, the AI key is used when it validates; otherwise the
    purchase has no key.
    """
    for code in sorted(codes, key=lambda c: not c.is_qr):
        key = _key_from_code(code)
        if key is not None and is_valid_access_key(key):
            source = AccessKeySource.QR_CODE if code.is_qr else AccessKeySource.BARCODE
            return AccessKeyOutcome(key=key, source=source)

    normalized_ai = _digits(ai_key) if ai_key is not None else None
    if normalized_ai and is_valid_access_key(normalized_ai):
        return AccessKeyOutcome(key=normalized_ai, source=AccessKeySource.AI)

    return AccessKeyOutcome()
