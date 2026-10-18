"""Webhook route tests for the two channels.

Baileys: a signed POST is authenticated and the message reaches the processor;
a bad signature returns 401. Cloud API: the GET handshake echoes the challenge,
a signed POST is acknowledged with 200 and the message is processed on a
background task, and a bad signature returns 401. No network is used.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.channel.port import InboundMessage
from app.channels.baileys import BaileysChannel
from app.channels.cloud_api import CloudApiChannel
from app.channels.settings import BaileysSettings, CloudApiSettings
from app.flow.webhook import baileys_router, cloud_api_router

_BAILEYS_WEBHOOK_SECRET = "webhook-secret"
_APP_SECRET = "app-secret"
_VERIFY_TOKEN = "verify-token"


class _Processor:
    def __init__(self) -> None:
        self.messages: list[InboundMessage] = []

    async def __call__(self, message: InboundMessage) -> None:
        self.messages.append(message)


def _baileys_channel() -> BaileysChannel:
    settings = BaileysSettings(
        adapter_url="http://adapter",
        adapter_secret="s",
        webhook_secret=_BAILEYS_WEBHOOK_SECRET,
    )
    return BaileysChannel(settings, httpx.AsyncClient(base_url="http://adapter"))


def _cloud_channel() -> CloudApiChannel:
    settings = CloudApiSettings(
        phone_number_id="pid",
        access_token="t",
        app_secret=_APP_SECRET,
        verify_token=_VERIFY_TOKEN,
        graph_version="v23.0",
    )
    return CloudApiChannel(settings, httpx.AsyncClient(base_url="https://graph.facebook.com"))


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _baileys_event() -> bytes:
    return json.dumps(
        {
            "event": "message",
            "id": "b-1",
            "senderJid": "5511999999999@s.whatsapp.net",
            "fromMe": False,
            "kind": "text",
            "text": "comprei arroz",
            "timestamp": 0,
        }
    ).encode()


def _cloud_event() -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "5511999999999",
                                        "id": "c-1",
                                        "timestamp": "0",
                                        "type": "text",
                                        "text": {"body": "comprei arroz"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()


def _client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_baileys_valid_post_reaches_processor() -> None:
    processor = _Processor()
    client = _client(baileys_router(_baileys_channel(), processor))
    body = _baileys_event()
    response = client.post(
        "/webhook", content=body, headers={"X-Signature-256": _sign(_BAILEYS_WEBHOOK_SECRET, body)}
    )
    assert response.status_code == 200
    assert [m.id for m in processor.messages] == ["b-1"]


def test_baileys_bad_signature_is_401() -> None:
    processor = _Processor()
    client = _client(baileys_router(_baileys_channel(), processor))
    response = client.post(
        "/webhook", content=_baileys_event(), headers={"X-Signature-256": "sha256=bad"}
    )
    assert response.status_code == 401
    assert processor.messages == []


def test_cloud_handshake_echoes_challenge() -> None:
    client = _client(cloud_api_router(_cloud_channel(), _Processor()))
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": _VERIFY_TOKEN,
            "hub.challenge": "CHALLENGE-9",
        },
    )
    assert response.status_code == 200
    assert response.text == "CHALLENGE-9"


def test_cloud_handshake_wrong_token_is_403() -> None:
    client = _client(cloud_api_router(_cloud_channel(), _Processor()))
    response = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
    )
    assert response.status_code == 403


def test_cloud_valid_post_is_acked_and_processed() -> None:
    processor = _Processor()
    client = _client(cloud_api_router(_cloud_channel(), processor))
    body = _cloud_event()
    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(_APP_SECRET, body)}
    )
    assert response.status_code == 200
    # TestClient runs the background task before returning, so it has processed.
    assert [m.id for m in processor.messages] == ["c-1"]


def test_cloud_bad_signature_is_401() -> None:
    processor = _Processor()
    client = _client(cloud_api_router(_cloud_channel(), processor))
    response = client.post(
        "/webhook", content=_cloud_event(), headers={"X-Hub-Signature-256": "sha256=bad"}
    )
    assert response.status_code == 401
    assert processor.messages == []
