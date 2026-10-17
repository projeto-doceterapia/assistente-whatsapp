"""Tests for image preprocessing.

Images are generated in-memory so the suite needs no fixtures on disk. The
tests prove EXIF is gone from the output, that orientation is applied, and
that non-images and oversize inputs are rejected.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.media import (
    ImageTooLargeError,
    PreprocessConfig,
    UnsupportedMediaError,
    preprocess_image,
)

# EXIF orientation tag id; value 6 means "rotate 90° CW to view upright".
_ORIENTATION_TAG = 0x0112


def _jpeg_bytes(size=(64, 48), color=(200, 50, 50), orientation: int | None = None) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    if orientation is not None:
        exif = image.getexif()
        exif[_ORIENTATION_TAG] = orientation
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _png_bytes(size=(64, 48)) -> bytes:
    image = Image.new("RGB", size, (10, 120, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _webp_bytes(size=(64, 48)) -> bytes:
    image = Image.new("RGB", size, (30, 180, 90))
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP")
    return buffer.getvalue()


def _has_exif(data: bytes) -> bool:
    with Image.open(io.BytesIO(data)) as image:
        return len(image.getexif()) > 0


def test_jpeg_output_has_no_exif():
    data = _jpeg_bytes(orientation=1)
    assert _has_exif(data)

    result = preprocess_image(data)

    assert result.mime == "image/jpeg"
    assert not _has_exif(result.data)


def test_orientation_applied_before_stripping():
    # A 64x48 landscape with orientation 6 must become 48x64 portrait once the
    # rotation is baked in, and carry no EXIF afterwards.
    data = _jpeg_bytes(size=(64, 48), orientation=6)

    result = preprocess_image(data)

    assert (result.width, result.height) == (48, 64)
    assert not _has_exif(result.data)


def test_png_is_accepted_and_sniffed_by_content():
    result = preprocess_image(_png_bytes())
    assert result.mime == "image/png"


def test_webp_is_accepted():
    result = preprocess_image(_webp_bytes())
    assert result.mime == "image/webp"


def test_resize_caps_longest_side():
    data = _jpeg_bytes(size=(4000, 2000))

    result = preprocess_image(data, PreprocessConfig(max_side_px=2048))

    assert max(result.width, result.height) == 2048
    # Aspect ratio preserved (2:1).
    assert result.width == 2048
    assert result.height == 1024


def test_small_image_is_not_upscaled():
    result = preprocess_image(_jpeg_bytes(size=(100, 80)), PreprocessConfig(max_side_px=2048))
    assert (result.width, result.height) == (100, 80)


def test_non_image_is_rejected():
    with pytest.raises(UnsupportedMediaError):
        preprocess_image(b"this is not an image at all")


def test_declared_header_does_not_bypass_content_sniff():
    # Bytes that lie about being a JPEG (fake magic) must still be rejected,
    # because the format is sniffed by decoding, not by the header string.
    fake = b"\xff\xd8\xff\xe0" + b"not really a jpeg body"
    with pytest.raises(UnsupportedMediaError):
        preprocess_image(fake)


def test_oversize_input_is_rejected():
    data = _jpeg_bytes(size=(64, 48))
    with pytest.raises(ImageTooLargeError):
        preprocess_image(data, PreprocessConfig(max_bytes=10))


def test_output_is_decodable():
    result = preprocess_image(_jpeg_bytes(size=(200, 100)))
    with Image.open(io.BytesIO(result.data)) as image:
        image.load()
        assert image.size == (200, 100)
