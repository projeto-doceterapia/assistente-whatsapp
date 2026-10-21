from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.channel.port import (
    ChannelPort,
    HealthStatus,
    InboundMessage,
    MediaNotFoundError,
)


@dataclass
class SentText:
    recipient: str
    text: str
    message_id: str


@dataclass
class SentNotification:
    recipient: str
    template_key: str
    parametros: dict[str, object]
    message_id: str


@dataclass
class FakeChannel(ChannelPort):
    """In-memory channel for tests. Records what was sent and serves staged media."""

    sent_texts: list[SentText] = field(default_factory=list)
    sent_notifications: list[SentNotification] = field(default_factory=list)
    inbound: list[InboundMessage] = field(default_factory=list)
    _media: dict[str, bytes] = field(default_factory=dict)
    _health: HealthStatus = HealthStatus.CONNECTED

    def stage_inbound(self, message: InboundMessage) -> None:
        self.inbound.append(message)

    def stage_media(self, media_ref: str, data: bytes) -> None:
        self._media[media_ref] = data

    def set_health(self, status: HealthStatus) -> None:
        self._health = status

    async def send_text(self, recipient: str, text: str) -> str:
        message_id = f"fake-{uuid.uuid4().hex}"
        self.sent_texts.append(SentText(recipient, text, message_id))
        return message_id

    async def send_notification(self, recipient: str, template_key: str, parametros: dict[str, object]) -> str:
        message_id = f"fake-{uuid.uuid4().hex}"
        self.sent_notifications.append(SentNotification(recipient, template_key, parametros, message_id))
        return message_id

    async def fetch_media(self, media_ref: str) -> bytes:
        try:
            return self._media[media_ref]
        except KeyError as exc:
            raise MediaNotFoundError(media_ref) from exc

    async def health(self) -> HealthStatus:
        return self._health
