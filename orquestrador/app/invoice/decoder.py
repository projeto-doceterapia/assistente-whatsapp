from __future__ import annotations

from typing import Any, Protocol

import zxingcpp

from app.invoice.models import DecodedCode, DecodeOutcome

_READ_FORMATS = (
    zxingcpp.BarcodeFormat.QRCode,
    zxingcpp.BarcodeFormat.Code128,
    zxingcpp.BarcodeFormat.ITF,
)


class DecodableImage(Protocol):
    """A decoded image accepted by ``zxingcpp.read_barcodes``.

    This is any object exposing the buffer / ``__array_interface__`` protocol
    with ``uint8`` pixels (a Pillow image, a NumPy array, a ``zxingcpp``
    ``ImageView`` or the output of ``create_barcode(...).to_image()``). The
    caller owns turning encoded file bytes into such an object; this module
    never imports an image-decoding library.
    """

    def __array_interface__(self) -> dict[str, Any]: ...


def decode_image(image: DecodableImage) -> DecodeOutcome:
    """Decode every NFC-e QR code and DANFE barcode in ``image``.

    This is the module's only I/O boundary: it delegates pixel reading to
    ``zxingcpp`` and returns typed codes, QR first by nature of the reader.
    Key extraction and validation stay in ``keys``.
    """
    results = zxingcpp.read_barcodes(image, formats=_READ_FORMATS)
    codes = [
        DecodedCode(
            text=result.text,
            is_qr=result.format == zxingcpp.BarcodeFormat.QRCode,
        )
        for result in results
        if result.text
    ]
    return DecodeOutcome(codes=codes)
