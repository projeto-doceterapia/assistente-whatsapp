"""Streaming media download with a hard byte cap.

Media is downloaded by streaming the response and counting bytes, so an
oversized body is aborted without being fully buffered. The declared
Content-Length is checked first (reject before reading any body), then the
bytes are counted while reading and the stream is aborted the moment it crosses
the cap even when the length was absent or lied.
"""

from __future__ import annotations

import httpx

from app.channel.port import MediaNotFoundError, MediaTooLargeError


async def download_capped(
    client: httpx.AsyncClient,
    url: str,
    *,
    media_ref: str,
    max_bytes: int,
    headers: dict[str, str] | None = None,
) -> bytes:
    """Stream `url` through `client`, returning its bytes or raising.

    Raises MediaNotFoundError on 404 and MediaTooLargeError as soon as the
    declared length or the streamed byte count exceeds `max_bytes`.
    """
    async with client.stream("GET", url, headers=headers) as response:
        if response.status_code == 404:
            raise MediaNotFoundError(media_ref)
        response.raise_for_status()

        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    raise MediaTooLargeError(media_ref, max_bytes)
            except ValueError:
                # A malformed Content-Length is ignored; the byte counter below
                # still enforces the cap while reading.
                pass

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise MediaTooLargeError(media_ref, max_bytes)
            chunks.append(chunk)
        return b"".join(chunks)
