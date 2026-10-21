"""In-test fixture generation for receipt code images.

Images are produced with ``zxingcpp`` itself (no Pillow, NumPy or ``qrcode``):
``create_barcode(...).to_image()`` yields a grayscale buffer that
``read_barcodes`` reads back. Multiple codes are tiled onto a single
``ImageView`` built from a stdlib ``bytearray`` canvas.
"""

from __future__ import annotations

import zxingcpp

# A real NFC-e access key whose modulo-11 check digit (2) is valid.
VALID_KEY = "35200114200166000187650010000000211000000212"
# Same body with a wrong check digit (7) -> modulo-11 fails.
CORRUPTED_KEY = "35200114200166000187650010000000211000000217"


def qr_image(payload: str, scale: int = 6) -> object:
    """Return a decodable QR-code image carrying ``payload``."""
    return zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.QRCode).to_image(
        scale=scale
    )


def code128_image(payload: str, scale: int = 3) -> object:
    """Return a decodable Code 128 barcode image carrying ``payload``."""
    return zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.Code128).to_image(
        scale=scale
    )


def blank_image(width: int = 200, height: int = 200) -> object:
    """Return an all-white image with no code in it."""
    data = bytes(b"\xff" * (width * height))
    return zxingcpp.ImageView(data, width, height, zxingcpp.ImageFormat.Lum, width, 1)


def stack_images(top: object, bottom: object, margin: int = 60, gap: int = 60) -> object:
    """Tile two grayscale code images onto one white ``ImageView`` canvas.

    This keeps the "multiple codes in one image" case free of Pillow/NumPy:
    each image's rows are copied into a ``bytearray`` with quiet zones.
    """
    mv_top = memoryview(top)
    mv_bottom = memoryview(bottom)
    th, tw = mv_top.shape
    bh, bw = mv_bottom.shape

    width = max(tw, bw) + 2 * margin
    height = margin + th + gap + bh + margin
    canvas = bytearray(b"\xff" * (width * height))

    def _blit(mv: memoryview, top_row: int, left_col: int) -> None:
        rows, cols = mv.shape
        src = mv.tobytes()
        for row in range(rows):
            offset = (top_row + row) * width + left_col
            canvas[offset : offset + cols] = src[row * cols : (row + 1) * cols]

    _blit(mv_top, margin, margin)
    _blit(mv_bottom, margin + th + gap, margin)

    return zxingcpp.ImageView(
        bytes(canvas), width, height, zxingcpp.ImageFormat.Lum, width, 1
    )
