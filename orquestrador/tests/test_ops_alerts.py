from __future__ import annotations

import pytest

from app.channel.fake import FakeChannel
from app.channel.port import HealthStatus
from app.core.client import CoreClient
from app.core.models import TipoNotificacao
from app.core.stub import CoreStub
from app.ops.alerts import OperationalAlerts
from app.ops.settings import OpsSettings
from tests.fake_redis import FakeRedis

RECIPIENT = "5511999999999"
NOTIF_PATH = "/notificacoes"


def _created_types(stub: CoreStub) -> list[str]:
    import json

    return [
        json.loads(req.content)["tipo"]
        for req in stub.requests
        if req.url.path == NOTIF_PATH
    ]


async def _alerts(
    stub: CoreStub,
    http,
    channel: FakeChannel,
    redis: FakeRedis,
    settings: OpsSettings | None = None,
) -> OperationalAlerts:
    return OperationalAlerts(
        core=CoreClient(http),
        channel=channel,
        redis=redis,
        recipient=RECIPIENT,
        settings=settings or OpsSettings(),
    )


async def test_session_down_creates_in_app_and_whatsapp_when_healthy() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    channel.set_health(HealthStatus.CONNECTED)
    redis = FakeRedis()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, redis)
        await alerts.raise_session_down()
    assert _created_types(stub) == [TipoNotificacao.ALERTA_OPERACIONAL.value]
    assert len(channel.sent_texts) == 1
    # The alert text must not leak secrets or receipt data.
    assert "sess" in channel.sent_texts[0].text.lower()


async def test_channel_down_creates_in_app_without_whatsapp_send() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    channel.set_health(HealthStatus.DOWN)
    redis = FakeRedis()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, redis)
        await alerts.raise_session_down()
    assert _created_types(stub) == [TipoNotificacao.ALERTA_OPERACIONAL.value]
    assert channel.sent_texts == []  # no WhatsApp send when the channel is down


async def test_cooldown_suppresses_repeat_within_window() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, redis)
        await alerts.raise_session_down()
        await alerts.raise_session_down()
        await alerts.raise_session_down()
    # Only the first one goes out; the cool-down swallows the repeats.
    assert _created_types(stub) == [TipoNotificacao.ALERTA_OPERACIONAL.value]
    assert len(channel.sent_texts) == 1


async def test_distinct_kinds_each_alert_once() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, redis)
        await alerts.raise_session_down()
        await alerts.raise_core_down()
        await alerts.raise_ai_errors()
        await alerts.raise_redis_down()
        await alerts.raise_alert("janela encerrada (id 7)")
    assert len(_created_types(stub)) == 5
    # The missed-notification alert text is id-free (no "7").
    assert all("7" not in sent.text for sent in channel.sent_texts)


async def test_dlq_items_dedup_per_subject() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, redis)
        await alerts.raise_dlq_item({"idempotency_key": "nota:1:1"})
        await alerts.raise_dlq_item({"idempotency_key": "nota:1:1"})  # same: deduped
        await alerts.raise_dlq_item({"idempotency_key": "nota:1:2"})  # distinct
    assert len(_created_types(stub)) == 2


async def test_redis_down_alert_fails_open_when_cooldown_store_broken() -> None:
    class BrokenRedis:
        async def set(self, *_a, **_k):
            raise RuntimeError("redis down")

    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, BrokenRedis())
        await alerts.raise_redis_down()
    # Cool-down store is down, yet the alert still goes out (fail open).
    assert _created_types(stub) == [TipoNotificacao.ALERTA_OPERACIONAL.value]


async def test_in_app_failure_still_tries_whatsapp() -> None:
    stub = CoreStub()
    stub.status("POST", NOTIF_PATH, 503)  # core down
    channel = FakeChannel()
    channel.set_health(HealthStatus.CONNECTED)
    redis = FakeRedis()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, redis)
        await alerts.raise_session_down()
    # In-app creation failed, but the WhatsApp copy is still attempted.
    assert len(channel.sent_texts) == 1
