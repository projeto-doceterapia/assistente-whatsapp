from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.channel.port import (
    ChannelPort,
    HealthStatus,
    InboundMessage,
    MediaTooLargeError,
    MessageKind,
)
from app.channels.baileys import BaileysChannel, InvalidSignatureError
from app.channels.settings import BaileysSettings
from tests.channel_contract import ChannelContract

_SECRET = "adapter-secret"
_WEBHOOK_SECRET = "webhook-secret"
_SECRET_HEADER = "X-Adapter-Secret"


class _FakeAdapter:
    """In-memory stand-in for the Baileys adapter HTTP API."""

    def __init__(self) -> None:
        self.media: dict[str, bytes] = {}
        self.sent: list[str] = []
        self.health_state = "connected"

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get(_SECRET_HEADER) != _SECRET and request.url.path != "/health":
            return httpx.Response(401, json={"error": "unauthorized"})
        if request.url.path == "/messages" and request.method == "POST":
            body = json.loads(request.content)
            self.sent.append(body["text"])
            return httpx.Response(202, json={"status": "queued"})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": self.health_state})
        if request.url.path.startswith("/media/"):
            ref = request.url.path.rsplit("/", 1)[-1]
            data = self.media.get(ref)
            if data is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, content=data, headers={"content-type": "image/png"})
        return httpx.Response(404)


def _make_channel(adapter: _FakeAdapter) -> BaileysChannel:
    transport = httpx.MockTransport(adapter.handler)
    client = httpx.AsyncClient(
        base_url="http://adapter",
        headers={_SECRET_HEADER: _SECRET},
        transport=transport,
    )
    settings = BaileysSettings(
        adapter_url="http://adapter",
        adapter_secret=_SECRET,
        webhook_secret=_WEBHOOK_SECRET,
    )
    return BaileysChannel(settings, client)


def _sign(body: bytes) -> str:
    digest = hmac.new(_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _event(message: InboundMessage, from_me: bool = False) -> bytes:
    kind = message.kind.value
    payload = {
        "event": "message",
        "id": message.id,
        "groupJid": "123@g.us",
        "senderJid": f"{message.sender}@s.whatsapp.net",
        "fromMe": from_me,
        "kind": kind,
        "text": message.text or "",
        "mediaRef": message.media_ref,
        "timestamp": int(message.timestamp.timestamp() * 1000),
    }
    return json.dumps(payload).encode()


class TestBaileysChannel(ChannelContract):
    def make_channel(self) -> ChannelPort:
        self._adapter = _FakeAdapter()
        self._inbox: list[InboundMessage] = []
        return _make_channel(self._adapter)

    def stage_inbound(self, channel: ChannelPort, message: InboundMessage) -> None:
        assert isinstance(channel, BaileysChannel)
        body = _event(message)
        parsed = channel.verify_and_parse(body, _sign(body))
        if parsed is not None:
            self._inbox.append(parsed)

    def stage_media(self, channel: ChannelPort, media_ref: str, data: bytes) -> None:
        self._adapter.media[media_ref] = data

    def received(self, channel: ChannelPort) -> list[InboundMessage]:
        return self._inbox


async def test_invalid_signature_is_rejected() -> None:
    channel = _make_channel(_FakeAdapter())
    body = b'{"event":"message","id":"x","kind":"text","senderJid":"55@s.whatsapp.net","timestamp":0}'
    with pytest.raises(InvalidSignatureError):
        channel.verify_and_parse(body, "sha256=deadbeef")


async def test_missing_signature_is_rejected() -> None:
    channel = _make_channel(_FakeAdapter())
    body = b'{"event":"message","id":"x","kind":"text"}'
    with pytest.raises(InvalidSignatureError):
        channel.verify_and_parse(body, None)


async def test_own_message_echo_is_dropped() -> None:
    adapter = _FakeAdapter()
    channel = _make_channel(adapter)
    message = InboundMessage(
        id="echo-1",
        sender="5511999999999",
        kind=MessageKind.TEXT,
        timestamp=datetime(2026, 6, 14, tzinfo=timezone.utc),
        text="enviado por mim",
    )
    body = _event(message, from_me=True)
    assert channel.verify_and_parse(body, _sign(body)) is None


async def test_session_event_is_not_a_message() -> None:
    channel = _make_channel(_FakeAdapter())
    body = json.dumps({"event": "session", "type": "logged_out", "timestamp": 0}).encode()
    assert channel.verify_and_parse(body, _sign(body)) is None


async def test_send_text_calls_adapter() -> None:
    adapter = _FakeAdapter()
    channel = _make_channel(adapter)
    message_id = await channel.send_text("5511999999999", "oi")
    assert message_id.startswith("baileys-")
    assert adapter.sent == ["oi"]


async def test_health_down_when_adapter_unreachable() -> None:
    def _fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("adapter unreachable", request=request)

    client = httpx.AsyncClient(
        base_url="http://adapter",
        headers={_SECRET_HEADER: _SECRET},
        transport=httpx.MockTransport(_fail),
    )
    settings = BaileysSettings(
        adapter_url="http://adapter", adapter_secret=_SECRET, webhook_secret=_WEBHOOK_SECRET
    )
    channel = BaileysChannel(settings, client)
    assert await channel.health() == HealthStatus.DOWN


def _oversized_channel(body: bytes, *, send_length: bool, max_bytes: int) -> BaileysChannel:
    def handler(request: httpx.Request) -> httpx.Response:
        if send_length:
            return httpx.Response(200, content=body)

        async def chunks():
            for i in range(0, len(body), 8):
                yield body[i : i + 8]

        return httpx.Response(200, content=chunks())

    client = httpx.AsyncClient(
        base_url="http://adapter",
        headers={_SECRET_HEADER: _SECRET},
        transport=httpx.MockTransport(handler),
    )
    settings = BaileysSettings(
        adapter_url="http://adapter",
        adapter_secret=_SECRET,
        webhook_secret=_WEBHOOK_SECRET,
        media_max_bytes=max_bytes,
    )
    return BaileysChannel(settings, client)


async def test_media_over_cap_rejected_with_content_length() -> None:
    channel = _oversized_channel(b"x" * 32, send_length=True, max_bytes=16)
    with pytest.raises(MediaTooLargeError):
        await channel.fetch_media("media-1")


async def test_media_over_cap_rejected_without_content_length() -> None:
    channel = _oversized_channel(b"x" * 32, send_length=False, max_bytes=16)
    with pytest.raises(MediaTooLargeError):
        await channel.fetch_media("media-1")
