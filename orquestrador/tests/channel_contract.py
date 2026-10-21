from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.channel.port import (
    ChannelPort,
    HealthStatus,
    InboundMessage,
    MediaNotFoundError,
    MessageKind,
)

E164_RECIPIENT = "5511999999999"


class ChannelContract:
    """Shared contract every channel implementation must satisfy.

    Subclasses provide `make_channel()` returning a ready channel, plus helpers
    to stage an inbound message and media so the same assertions run against the
    fake, Baileys and Cloud API channels.
    """

    def make_channel(self) -> ChannelPort:
        raise NotImplementedError

    def stage_inbound(self, channel: ChannelPort, message: InboundMessage) -> None:
        raise NotImplementedError

    def stage_media(self, channel: ChannelPort, media_ref: str, data: bytes) -> None:
        raise NotImplementedError

    def received(self, channel: ChannelPort) -> list[InboundMessage]:
        raise NotImplementedError

    def _message(self, **overrides: object) -> InboundMessage:
        base: dict[str, object] = {
            "id": "m-1",
            "sender": E164_RECIPIENT,
            "kind": MessageKind.TEXT,
            "timestamp": datetime(2026, 6, 14, 8, 0, tzinfo=timezone.utc),
            "text": "Comprei 2 kg de acucar",
        }
        base.update(overrides)
        return InboundMessage(**base)

    @pytest.mark.parametrize(
        "kind,text,media_ref",
        [
            (MessageKind.TEXT, "Comprei 2 kg de acucar", None),
            (MessageKind.IMAGE, None, "media-1"),
            (MessageKind.UNSUPPORTED, None, None),
        ],
    )
    async def test_normalized_inbound(
        self, kind: MessageKind, text: str | None, media_ref: str | None
    ) -> None:
        channel = self.make_channel()
        message = self._message(id="in-1", kind=kind, text=text, media_ref=media_ref)
        self.stage_inbound(channel, message)

        received = self.received(channel)
        assert len(received) == 1
        got = received[0]
        assert got.id == "in-1"
        assert got.sender == E164_RECIPIENT
        assert got.kind == kind
        assert got.text == text
        assert got.media_ref == media_ref
        assert got.timestamp.tzinfo is not None

    async def test_send_text_returns_message_id(self) -> None:
        channel = self.make_channel()
        message_id = await channel.send_text(E164_RECIPIENT, "oi")
        assert isinstance(message_id, str) and message_id

    async def test_send_notification_returns_message_id(self) -> None:
        channel = self.make_channel()
        message_id = await channel.send_notification(
            E164_RECIPIENT, "resumo_fim_dia", {"itens": 3}
        )
        assert isinstance(message_id, str) and message_id

    async def test_fetch_media_returns_bytes(self) -> None:
        channel = self.make_channel()
        self.stage_media(channel, "media-1", b"\x89PNG")
        data = await channel.fetch_media("media-1")
        assert data == b"\x89PNG"

    async def test_fetch_unknown_media_raises(self) -> None:
        channel = self.make_channel()
        with pytest.raises(MediaNotFoundError):
            await channel.fetch_media("missing")

    async def test_health_reports_status(self) -> None:
        channel = self.make_channel()
        assert await channel.health() in {
            HealthStatus.CONNECTED,
            HealthStatus.DEGRADED,
            HealthStatus.DOWN,
        }
