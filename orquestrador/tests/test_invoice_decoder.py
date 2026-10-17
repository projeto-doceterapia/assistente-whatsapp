from __future__ import annotations

from app.invoice import (
    AccessKeySource,
    decode_image,
    resolve_access_key,
)
from tests.invoice_fixtures import (
    VALID_KEY,
    blank_image,
    code128_image,
    qr_image,
    stack_images,
)


def test_decode_nfce_qr_and_resolve_key() -> None:
    image = qr_image(f"https://www.nfce.fazenda.sp.gov.br/qrcode?p={VALID_KEY}|2|1|1|AB")
    outcome = decode_image(image)
    assert len(outcome.codes) == 1
    assert outcome.codes[0].is_qr is True

    key_outcome = resolve_access_key(outcome.codes)
    assert key_outcome.key == VALID_KEY
    assert key_outcome.source is AccessKeySource.QR_CODE


def test_decode_danfe_code128_and_resolve_key() -> None:
    image = code128_image(VALID_KEY)
    outcome = decode_image(image)
    assert any(not code.is_qr for code in outcome.codes)

    key_outcome = resolve_access_key(outcome.codes)
    assert key_outcome.key == VALID_KEY
    assert key_outcome.source is AccessKeySource.BARCODE


def test_decode_blank_image_finds_no_code() -> None:
    outcome = decode_image(blank_image())
    assert outcome.codes == []

    key_outcome = resolve_access_key(outcome.codes)
    assert key_outcome.has_key is False


def test_decode_multiple_codes_in_one_image_prefers_qr() -> None:
    qr = qr_image(f"https://x?p={VALID_KEY}|2|1")
    bar = code128_image(VALID_KEY)
    image = stack_images(qr, bar)

    outcome = decode_image(image)
    assert any(code.is_qr for code in outcome.codes)
    assert any(not code.is_qr for code in outcome.codes)

    key_outcome = resolve_access_key(outcome.codes)
    assert key_outcome.key == VALID_KEY
    assert key_outcome.source is AccessKeySource.QR_CODE
