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
from app.channels.cloud_api import (
    CloudApiChannel,
    InvalidSignatureError,
    UntrustedMediaHostError,
)
from app.channels.settings import CloudApiSettings
from tests.channel_contract import ChannelContract

_PHONE_ID = "123456789"
_TOKEN = "cloud-token"
_APP_SECRET = "app-secret"
_VERIFY_TOKEN = "verify-token"
_VERSION = "v23.0"


class _FakeGraph:
    """In-memory Meta Graph API stand-in for the message and media endpoints."""

    def __init__(self) -> None:
        self.media: dict[str, bytes] = {}
        self.sent: list[dict] = []
        self._counter = 0
        # Records the Authorization header seen on each CDN download, so a test
        # can assert the bearer token never reaches the media host.
        self.download_auth_headers: list[str | None] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/download/"):
            # The CDN serves the bytes without the Graph bearer token.
            self.download_auth_headers.append(request.headers.get("Authorization"))
            ref = path.rsplit("/", 1)[-1]
            data = self.media.get(ref)
            return httpx.Response(200, content=data) if data else httpx.Response(404)
        if request.headers.get("Authorization") != f"Bearer {_TOKEN}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if path.endswith("/messages") and request.method == "POST":
            self.sent.append(json.loads(request.content))
            self._counter += 1
            return httpx.Response(
                200,
                json={
                    "messaging_product": "whatsapp",
                    "messages": [{"id": f"wamid.TEST{self._counter}"}],
                },
            )
        # Media id -> metadata with a download URL (step 1 of the two-step flow).
        ref = path.rsplit("/", 1)[-1]
        if ref in self.media:
            return httpx.Response(
                200,
                json={
                    "url": f"https://graph.facebook.com/download/{ref}",
                    "mime_type": "image/jpeg",
                    "id": ref,
                },
            )
        if ref == _PHONE_ID:
            return httpx.Response(200, json={"id": _PHONE_ID})
        return httpx.Response(404, json={"error": {"message": "not found"}})


def _make_channel(graph: _FakeGraph) -> CloudApiChannel:
    transport = httpx.MockTransport(graph.handler)
    client = httpx.AsyncClient(
        base_url="https://graph.facebook.com",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        transport=transport,
    )
    # The media client shares the fake transport but carries no Authorization,
    # mirroring the production split where the CDN download drops the token.
    media_client = httpx.AsyncClient(transport=httpx.MockTransport(graph.handler))
    settings = CloudApiSettings(
        phone_number_id=_PHONE_ID,
        access_token=_TOKEN,
        app_secret=_APP_SECRET,
        verify_token=_VERIFY_TOKEN,
        graph_version=_VERSION,
    )
    return CloudApiChannel(settings, client, media_client=media_client)


def _sign(body: bytes) -> str:
    digest = hmac.new(_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _inbound_payload(message: InboundMessage) -> dict:
    # Recorded from Meta's documented webhook payload shapes (see README note).
    if message.kind is MessageKind.TEXT:
        content = {"type": "text", "text": {"body": message.text}}
    elif message.kind is MessageKind.IMAGE:
        content = {"type": "image", "image": {"id": message.media_ref, "mime_type": "image/jpeg"}}
    else:
        content = {"type": "sticker", "sticker": {"id": "stk-1"}}
    msg = {
        "from": message.sender,
        "id": message.id,
        "timestamp": str(int(message.timestamp.timestamp())),
        **content,
    }
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": _PHONE_ID},
                            "messages": [msg],
                        },
                    }
                ],
            }
        ],
    }


class TestCloudApiChannel(ChannelContract):
    def make_channel(self) -> ChannelPort:
        self._graph = _FakeGraph()
        self._inbox: list[InboundMessage] = []
        return _make_channel(self._graph)

    def stage_inbound(self, channel: ChannelPort, message: InboundMessage) -> None:
        assert isinstance(channel, CloudApiChannel)
        body = json.dumps(_inbound_payload(message)).encode()
        self._inbox.extend(channel.parse_event(body, _sign(body)))

    def stage_media(self, channel: ChannelPort, media_ref: str, data: bytes) -> None:
        self._graph.media[media_ref] = data

    def received(self, channel: ChannelPort) -> list[InboundMessage]:
        return self._inbox


async def test_handshake_echoes_challenge() -> None:
    channel = _make_channel(_FakeGraph())
    assert channel.verify_handshake("subscribe", _VERIFY_TOKEN, "CHALLENGE-1") == "CHALLENGE-1"


async def test_handshake_wrong_token_is_rejected() -> None:
    channel = _make_channel(_FakeGraph())
    with pytest.raises(InvalidSignatureError):
        channel.verify_handshake("subscribe", "wrong", "CHALLENGE-1")


async def test_invalid_signature_is_rejected() -> None:
    channel = _make_channel(_FakeGraph())
    body = json.dumps(_inbound_payload(_text_message())).encode()
    with pytest.raises(InvalidSignatureError):
        channel.parse_event(body, "sha256=deadbeef")


async def test_status_only_event_yields_no_messages() -> None:
    channel = _make_channel(_FakeGraph())
    body = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {"changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "delivered"}]}}]}
            ],
        }
    ).encode()
    assert channel.parse_event(body, _sign(body)) == []


async def test_send_text_posts_text_payload() -> None:
    graph = _FakeGraph()
    channel = _make_channel(graph)
    message_id = await channel.send_text("5511999999999", "oi")
    assert message_id == "wamid.TEST1"
    assert graph.sent[0]["type"] == "text"
    assert graph.sent[0]["text"]["body"] == "oi"


async def test_send_notification_posts_template_without_newlines() -> None:
    graph = _FakeGraph()
    channel = _make_channel(graph)
    await channel.send_notification(
        "5511999999999", "estoque_baixo", {"insumo": "Açúcar", "saldo": "1 kg", "minimo": "2 kg"}
    )
    payload = graph.sent[0]
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "estoque_baixo"
    assert payload["template"]["language"]["code"] == "pt_BR"
    for parameter in payload["template"]["components"][0]["parameters"]:
        assert "\n" not in parameter["text"]


def _text_message() -> InboundMessage:
    return InboundMessage(
        id="in-text",
        sender="5511999999999",
        kind=MessageKind.TEXT,
        timestamp=datetime(2026, 6, 14, tzinfo=timezone.utc),
        text="oi",
    )


async def test_media_download_does_not_send_bearer_token() -> None:
    graph = _FakeGraph()
    graph.media["media-1"] = b"\x89PNG-bytes"
    channel = _make_channel(graph)
    data = await channel.fetch_media("media-1")
    assert data == b"\x89PNG-bytes"
    # The CDN download carried no Authorization header.
    assert graph.download_auth_headers == [None]


async def test_media_download_rejects_foreign_host() -> None:
    graph = _FakeGraph()
    graph.media["media-1"] = b"\x89PNG"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        ref = path.rsplit("/", 1)[-1]
        if ref == "media-1" and not path.startswith("/download/"):
            # Step 1 points the download at a host outside the allowlist.
            return httpx.Response(
                200, json={"url": "https://evil.example.com/download/media-1"}
            )
        return httpx.Response(200, content=b"\x89PNG")

    client = httpx.AsyncClient(
        base_url="https://graph.facebook.com",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        transport=httpx.MockTransport(handler),
    )
    media_calls: list[str] = []

    def media_handler(request: httpx.Request) -> httpx.Response:
        media_calls.append(str(request.url))
        return httpx.Response(200, content=b"\x89PNG")

    media_client = httpx.AsyncClient(transport=httpx.MockTransport(media_handler))
    channel = CloudApiChannel(
        CloudApiSettings(
            phone_number_id=_PHONE_ID,
            access_token=_TOKEN,
            app_secret=_APP_SECRET,
            verify_token=_VERIFY_TOKEN,
            graph_version=_VERSION,
        ),
        client,
        media_client=media_client,
    )
    with pytest.raises(UntrustedMediaHostError):
        await channel.fetch_media("media-1")
    # The foreign host was never contacted.
    assert media_calls == []


async def test_media_download_rejects_non_https_host() -> None:
    graph = _FakeGraph()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"url": "http://graph.facebook.com/download/media-1"})

    client = httpx.AsyncClient(
        base_url="https://graph.facebook.com",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        transport=httpx.MockTransport(handler),
    )
    channel = CloudApiChannel(
        CloudApiSettings(
            phone_number_id=_PHONE_ID,
            access_token=_TOKEN,
            app_secret=_APP_SECRET,
            verify_token=_VERIFY_TOKEN,
            graph_version=_VERSION,
        ),
        client,
        media_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(UntrustedMediaHostError):
        await channel.fetch_media("media-1")


async def test_media_over_cap_rejected_with_content_length() -> None:
    oversized = b"x" * 32
    channel = _oversized_channel(oversized, send_length=True, max_bytes=16)
    with pytest.raises(MediaTooLargeError):
        await channel.fetch_media("media-1")


async def test_media_over_cap_rejected_without_content_length() -> None:
    oversized = b"x" * 32
    channel = _oversized_channel(oversized, send_length=False, max_bytes=16)
    with pytest.raises(MediaTooLargeError):
        await channel.fetch_media("media-1")


def _oversized_channel(body: bytes, *, send_length: bool, max_bytes: int) -> CloudApiChannel:
    def graph_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"url": "https://graph.facebook.com/download/media-1"})

    def media_handler(request: httpx.Request) -> httpx.Response:
        if send_length:
            return httpx.Response(200, content=body)
        # Stream the body in chunks with no Content-Length header, so the cap is
        # enforced only by counting bytes while reading.
        async def chunks():
            for i in range(0, len(body), 8):
                yield body[i : i + 8]

        return httpx.Response(200, content=chunks())

    client = httpx.AsyncClient(
        base_url="https://graph.facebook.com",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        transport=httpx.MockTransport(graph_handler),
    )
    media_client = httpx.AsyncClient(transport=httpx.MockTransport(media_handler))
    return CloudApiChannel(
        CloudApiSettings(
            phone_number_id=_PHONE_ID,
            access_token=_TOKEN,
            app_secret=_APP_SECRET,
            verify_token=_VERIFY_TOKEN,
            graph_version=_VERSION,
            media_max_bytes=max_bytes,
        ),
        client,
        media_client=media_client,
    )
