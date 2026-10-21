"""Image preprocessing for receipt photos.

The pipeline:
  1. sniff the real format from the bytes, not from any declared header, and
     reject anything that is not JPEG, PNG or WebP;
  2. reject inputs over the byte-size limit before decoding further;
  3. apply the EXIF orientation so the image is upright;
  4. resize so the longest side fits the limit;
  5. re-encode without any EXIF or other metadata (removes GPS location).

Step 3 runs before step 5 on purpose: once metadata is stripped the
orientation tag is gone, so the rotation must be baked into the pixels first.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps

# Accepted decoded formats. The key is PIL's format name; the value is the
# canonical MIME type reported back to the caller.
_ACCEPTED: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class MediaError(ValueError):
    """Base error for media preprocessing."""


class UnsupportedMediaError(MediaError):
    """The bytes are not a supported image (sniffed by content)."""


class ImageTooLargeError(MediaError):
    """The input exceeds the configured byte-size limit."""


@dataclass(frozen=True)
class PreprocessConfig:
    """Limits for preprocessing.

    max_bytes: reject inputs larger than this before heavy decoding.
    max_side_px: longest side after resize (e.g. 2048).
    """

    max_bytes: int = 8 * 1024 * 1024
    max_side_px: int = 2048


@dataclass(frozen=True)
class PreprocessedImage:
    """The cleaned image, ready to hand to the AI layer."""

    data: bytes
    mime: str
    width: int
    height: int


def preprocess_image(data: bytes, config: PreprocessConfig | None = None) -> PreprocessedImage:
    """Validate, normalize and strip metadata from `data`.

    Raises ImageTooLargeError when the input is too big and
    UnsupportedMediaError when it is not a supported image.
    """
    cfg = config or PreprocessConfig()

    if len(data) > cfg.max_bytes:
        raise ImageTooLargeError("imagem acima do tamanho máximo")

    image = _open_supported(data)
    try:
        # Bake the EXIF orientation into the pixels while the tag still exists.
        upright = ImageOps.exif_transpose(image)
        resized = _resize_to_max_side(upright, cfg.max_side_px)
        return _encode_without_metadata(resized, image.format)
    finally:
        image.close()


def _open_supported(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, ValueError) as exc:
        raise UnsupportedMediaError("formato de imagem não suportado") from exc

    if image.format not in _ACCEPTED:
        fmt = image.format
        image.close()
        raise UnsupportedMediaError(f"formato de imagem não suportado: {fmt}")
    return image


def _resize_to_max_side(image: Image.Image, max_side_px: int) -> Image.Image:
    longest = max(image.size)
    if longest <= max_side_px:
        return image
    scale = max_side_px / longest
    new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _encode_without_metadata(image: Image.Image, fmt: str | None) -> PreprocessedImage:
    # Pasting the pixels into a fresh image leaves behind the source info/EXIF
    # dictionary, so re-encoding carries no metadata. We pass no exif/icc/xmp.
    clean = Image.new(image.mode, image.size)
    clean.paste(image)

    out_format = fmt if fmt in _ACCEPTED else "JPEG"
    if out_format == "JPEG" and clean.mode not in ("RGB", "L"):
        clean = clean.convert("RGB")

    buffer = io.BytesIO()
    clean.save(buffer, format=out_format)
    return PreprocessedImage(
        data=buffer.getvalue(),
        mime=_ACCEPTED[out_format],
        width=clean.width,
        height=clean.height,
    )
