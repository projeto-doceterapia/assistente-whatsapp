from __future__ import annotations

import os
from dataclasses import dataclass


class ChannelSettingsError(RuntimeError):
    """Raised when a channel is selected without the settings it needs."""

    def __init__(self, name: str) -> None:
        super().__init__(f"missing required channel setting: {name}")
        self.name = name


def _get(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        value = value.strip()
    return value or None


def _require(name: str) -> str:
    value = _get(name)
    if not value:
        raise ChannelSettingsError(name)
    return value


# Hard cap on a downloaded media body, shared by every channel. The default
# matches the image preprocessing limit (8 MiB), so a receipt photo that the
# pipeline would reject anyway is never fully buffered first.
_MEDIA_MAX_BYTES_DEFAULT = 8 * 1024 * 1024

# Host suffixes the Cloud API is allowed to download media from. The first step
# of the Graph two-step flow returns a CDN URL; only these Meta-owned suffixes
# are trusted, and only over HTTPS.
_CLOUD_API_MEDIA_HOSTS_DEFAULT = (".fbcdn.net", ".facebook.com", ".whatsapp.net")


def _media_max_bytes() -> int:
    raw = _get("MEDIA_MAX_BYTES")
    if raw is None:
        return _MEDIA_MAX_BYTES_DEFAULT
    try:
        value = int(raw)
    except ValueError as exc:
        raise ChannelSettingsError("MEDIA_MAX_BYTES") from exc
    if value <= 0:
        raise ChannelSettingsError("MEDIA_MAX_BYTES")
    return value


def _cloud_api_media_hosts() -> tuple[str, ...]:
    raw = _get("CLOUD_API_MEDIA_HOSTS")
    if raw is None:
        return _CLOUD_API_MEDIA_HOSTS_DEFAULT
    hosts = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return hosts or _CLOUD_API_MEDIA_HOSTS_DEFAULT


@dataclass(frozen=True)
class BaileysSettings:
    """Settings for the Baileys channel, read from the environment.

    Kept in this package so app/config.py stays free of channel credentials,
    which are read lazily only when the Baileys channel is actually built.
    """

    adapter_url: str
    adapter_secret: str
    webhook_secret: str
    media_max_bytes: int = _MEDIA_MAX_BYTES_DEFAULT

    @staticmethod
    def from_env() -> "BaileysSettings":
        return BaileysSettings(
            adapter_url=_require("ADAPTER_URL").rstrip("/"),
            adapter_secret=_require("ADAPTER_SECRET"),
            webhook_secret=_require("WEBHOOK_SECRET"),
            media_max_bytes=_media_max_bytes(),
        )


@dataclass(frozen=True)
class CloudApiSettings:
    """Settings for the Meta Cloud API channel, read from the environment."""

    phone_number_id: str
    access_token: str
    app_secret: str
    verify_token: str
    graph_version: str
    media_max_bytes: int = _MEDIA_MAX_BYTES_DEFAULT
    media_hosts: tuple[str, ...] = _CLOUD_API_MEDIA_HOSTS_DEFAULT

    @staticmethod
    def from_env() -> "CloudApiSettings":
        return CloudApiSettings(
            phone_number_id=_require("CLOUD_API_PHONE_ID"),
            access_token=_require("CLOUD_API_TOKEN"),
            app_secret=_require("CLOUD_API_APP_SECRET"),
            verify_token=_require("CLOUD_API_VERIFY_TOKEN"),
            # Pinned to a current Graph API version; override only when needed.
            graph_version=_get("CLOUD_API_GRAPH_VERSION") or "v23.0",
            media_max_bytes=_media_max_bytes(),
            media_hosts=_cloud_api_media_hosts(),
        )


def channel_name() -> str:
    return _get("CHANNEL") or "baileys"
