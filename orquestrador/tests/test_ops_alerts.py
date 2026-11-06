from __future__ import annotations

import pytest

from app.channel.fake import FakeChannel
from app.channel.port import HealthStatus
from app.core.client import HEADER_TOKEN, CoreClient
from app.core.models import TipoNotificacao
from app.core.stub import CoreStub
from app.core.switches import AssistantSwitches
from app.ops.alerts import OperationalAlerts
from app.ops.settings import OpsSettings
from app.templates import load_default_renderer
from tests.fake_redis import FakeRedis

RECIPIENT = "5511900000001"
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
    assert len(channel.sent_notifications) == 1
    # The alert text must not leak secrets or receipt data.
    sent = channel.sent_notifications[0]
    assert sent.recipient == RECIPIENT
    assert sent.template_key == "alerta_operacional"
    assert "sess" in str(sent.parametros["mensagem"]).lower()


@pytest.mark.parametrize(
    "corpo",
    [
        {"avisosAutomaticosAtivo": False, "assistenteAtivo": True},
        {"avisosAutomaticosAtivo": True, "assistenteAtivo": False},
    ],
)
async def test_switch_off_skips_whatsapp(corpo: dict[str, bool]) -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    stub.json("GET", "/configuracao-notificacao", 200, corpo)
    channel = FakeChannel()
    channel.set_health(HealthStatus.CONNECTED)
    async with stub.client() as http:
        core = CoreClient(http)
        alerts = OperationalAlerts(
            core=core,
            channel=channel,
            redis=FakeRedis(),
            recipient=RECIPIENT,
            settings=OpsSettings(),
            switches=AssistantSwitches(core),
        )
        await alerts.raise_session_down()
    assert _created_types(stub) == [TipoNotificacao.ALERTA_OPERACIONAL.value]
    assert channel.sent_notifications == []


async def test_switches_on_still_send_whatsapp() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    stub.json(
        "GET",
        "/configuracao-notificacao",
        200,
        {"avisosAutomaticosAtivo": True, "assistenteAtivo": True},
    )
    channel = FakeChannel()
    channel.set_health(HealthStatus.CONNECTED)
    async with stub.client() as http:
        core = CoreClient(http)
        alerts = OperationalAlerts(
            core=core,
            channel=channel,
            redis=FakeRedis(),
            recipient=RECIPIENT,
            settings=OpsSettings(),
            switches=AssistantSwitches(core),
        )
        await alerts.raise_session_down()
    assert len(channel.sent_notifications) == 1


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
    assert channel.sent_notifications == []  # no WhatsApp send when the channel is down


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
    assert len(channel.sent_notifications) == 1


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
    assert all("7" not in str(sent.parametros) for sent in channel.sent_notifications)


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
    assert len(channel.sent_notifications) == 1


async def test_alert_uses_the_template_and_never_free_text() -> None:
    # Free text is refused outside the Cloud API 24 h window.
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, FakeRedis())
        await alerts.raise_core_down()
    assert channel.sent_texts == []
    assert [sent.template_key for sent in channel.sent_notifications] == ["alerta_operacional"]


async def test_alert_message_renders_without_a_doubled_period() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, FakeRedis())
        await alerts.raise_session_down()
    parts = load_default_renderer().render_parts(
        "alerta_operacional", channel.sent_notifications[0].parametros
    )
    assert len(parts) == 1
    assert parts[0].startswith("Alerta operacional: A sessão do WhatsApp")
    assert not parts[0].endswith(".")


async def test_credential_alert_message_has_no_secret() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    channel = FakeChannel()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, FakeRedis())
        await alerts.raise_core_auth_rejected()
    mensagem = str(channel.sent_notifications[0].parametros["mensagem"])
    assert mensagem.startswith("O sistema de estoque recusou a credencial do assistente")
    assert "até a configuração" in mensagem
    assert "token-ficticio" not in mensagem
    assert HEADER_TOKEN not in mensagem


async def test_credential_alert_reaches_whatsapp_when_the_in_app_copy_is_refused() -> None:
    stub = CoreStub()
    stub.status("POST", NOTIF_PATH, 401)
    channel = FakeChannel()
    async with stub.client() as http:
        alerts = await _alerts(stub, http, channel, FakeRedis())
        await alerts.raise_core_auth_rejected()
        await alerts.raise_core_auth_rejected()
    assert len(channel.sent_notifications) == 1
