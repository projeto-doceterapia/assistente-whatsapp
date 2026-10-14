from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class MessageKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    UNSUPPORTED = "unsupported"


class HealthStatus(str, Enum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DOWN = "down"


class InboundMessage(BaseModel):
    """A channel-agnostic inbound message. Every channel normalizes to this shape."""

    id: str
    sender: str
    kind: MessageKind
    timestamp: datetime
    text: str | None = None
    media_ref: str | None = None


class ChannelError(RuntimeError):
    """Base error for channel operations."""


class MediaNotFoundError(ChannelError):
    def __init__(self, media_ref: str) -> None:
        super().__init__("media not found")
        self.media_ref = media_ref


@runtime_checkable
class ChannelPort(Protocol):
    """The port every channel (fake, Baileys, Cloud API) implements identically."""

    async def send_text(self, recipient: str, text: str) -> str:
        """Send free text. Returns the channel message id."""
        ...

    async def send_notification(self, recipient: str, template_key: str, parametros: dict[str, object]) -> str:
        """Send a proactive notification rendered from a template. Returns the channel message id."""
        ...

    async def fetch_media(self, media_ref: str) -> bytes:
        """Fetch the raw bytes of a media reference. Raises MediaNotFoundError when unknown."""
        ...

    async def health(self) -> HealthStatus:
        """Report the channel health."""
        ...
