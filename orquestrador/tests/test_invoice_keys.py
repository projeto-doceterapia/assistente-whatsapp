from __future__ import annotations

import pytest

from app.invoice import (
    AccessKeySource,
    DecodedCode,
    extract_key_from_barcode_text,
    extract_key_from_qr_payload,
    is_valid_access_key,
    resolve_access_key,
)
from tests.invoice_fixtures import CORRUPTED_KEY, VALID_KEY


def test_valid_key_passes_modulo_11() -> None:
    assert is_valid_access_key(VALID_KEY) is True


def test_corrupted_key_fails_modulo_11() -> None:
    assert is_valid_access_key(CORRUPTED_KEY) is False


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        "123",
        VALID_KEY[:-1],  # 43 digits
        VALID_KEY + "0",  # 45 digits
        "3520011420016600018765001000000021100000021X",  # non-digit
    ],
)
def test_malformed_keys_are_invalid(key: str | None) -> None:
    assert is_valid_access_key(key) is False


@pytest.mark.parametrize(
    "payload",
    [
        f"https://www.nfce.fazenda.sp.gov.br/qrcode?p={VALID_KEY}|2|1|1|ABCDEF",
        f"http://nfe.sefaz.xx/consulta?chNFe={VALID_KEY}&tpAmb=1",
        f"HTTPS://X?P={VALID_KEY}|2|1",  # upper-case scheme/host, standard param case
        VALID_KEY,  # bare key with no URL
        f"{VALID_KEY}|2|1|1|hash",  # query fragment only, no scheme
    ],
)
def test_extract_key_from_qr_variants(payload: str) -> None:
    assert extract_key_from_qr_payload(payload) == VALID_KEY


def test_qr_p_parameter_wins_over_trailing_digits() -> None:
    payload = f"https://x?p={VALID_KEY}|2|1|1|99999999999999999999999999999999999999999999"
    assert extract_key_from_qr_payload(payload) == VALID_KEY


def test_extract_key_from_qr_without_key_returns_none() -> None:
    assert extract_key_from_qr_payload("https://x?foo=bar") is None


def test_extract_key_from_barcode_text() -> None:
    assert extract_key_from_barcode_text(VALID_KEY) == VALID_KEY
    assert extract_key_from_barcode_text(f" {VALID_KEY} ") == VALID_KEY
    assert extract_key_from_barcode_text("123") is None


def test_resolve_prefers_qr_over_barcode() -> None:
    other_valid = "35200114200166000187650010000000211000000212"
    codes = [
        DecodedCode(text=CORRUPTED_KEY, is_qr=False),
        DecodedCode(text=f"https://x?p={other_valid}|2|1", is_qr=True),
    ]
    outcome = resolve_access_key(codes)
    assert outcome.key == other_valid
    assert outcome.source is AccessKeySource.QR_CODE


def test_resolve_uses_barcode_when_no_qr() -> None:
    codes = [DecodedCode(text=VALID_KEY, is_qr=False)]
    outcome = resolve_access_key(codes)
    assert outcome.key == VALID_KEY
    assert outcome.source is AccessKeySource.BARCODE


def test_resolve_falls_back_to_valid_ai_key() -> None:
    outcome = resolve_access_key([], ai_key=VALID_KEY)
    assert outcome.key == VALID_KEY
    assert outcome.source is AccessKeySource.AI


def test_resolve_rejects_invalid_ai_key() -> None:
    outcome = resolve_access_key([], ai_key=CORRUPTED_KEY)
    assert outcome.has_key is False
    assert outcome.source is None


def test_decoded_code_wins_over_ai_key() -> None:
    codes = [DecodedCode(text=VALID_KEY, is_qr=False)]
    outcome = resolve_access_key(codes, ai_key=CORRUPTED_KEY)
    assert outcome.key == VALID_KEY
    assert outcome.source is AccessKeySource.BARCODE


def test_resolve_without_any_source_has_no_key() -> None:
    outcome = resolve_access_key([])
    assert outcome.has_key is False
    assert outcome.key is None
    assert outcome.source is None
