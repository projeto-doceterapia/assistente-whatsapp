from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from app.channel.fake import FakeChannel
from app.core.client import CoreClient
from app.core.stub import CoreStub
from app.delivery.settings import DeliverySettings
from app.delivery.state import DeliveryState
from app.delivery.worker import DeliveryWorker, create_delivery_worker
from tests.fake_redis import FakeRedis

RECIPIENT = "5511999999999"
WINDOW_START = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 6, 15, 8, 10, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class FixedRandom:
    """Returns a fixed fraction of the span, so the chosen send time is known."""

    def __init__(self, fraction: float) -> None:
        self._fraction = fraction

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self._fraction


class RecordingAlerts:
    def __init__(self) -> None:
        self.mensagens: list[str] = []

    async def raise_alert(self, mensagem: str) -> None:
        self.mensagens.append(mensagem)


def _agenda_item(
    notification_id: int = 1,
    inicio: datetime = WINDOW_START,
    fim: datetime = WINDOW_END,
    template_key: str = "resumo_inicio_dia",
    parametros: str | None = "{}",
) -> dict[str, object]:
    return {
        "idNotificacao": notification_id,
        "janelaInicio": inicio.isoformat(),
        "janelaFim": fim.isoformat(),
        "templateKey": template_key,
        "parametros": parametros,
    }


def _settings() -> DeliverySettings:
    # base 0 keeps the confirmation backoff from sleeping in tests.
    return DeliverySettings(confirm_base_s=0, confirm_cap_s=0, confirm_max_tentativas=3)


def _worker(
    *,
    core: CoreClient,
    channel: FakeChannel,
    redis: FakeRedis,
    clock: FakeClock,
    fraction: float = 0.5,
    alerts: RecordingAlerts | None = None,
) -> DeliveryWorker:
    return DeliveryWorker(
        core=core,
        channel=channel,
        state=DeliveryState(redis),
        recipient=RECIPIENT,
        settings=_settings(),
        clock=clock,
        random_source=FixedRandom(fraction),
        alerts=alerts,
    )


async def test_open_window_sends_and_confirms_enviada() -> None:
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    # now is past the chosen send time (fraction 0.5 -> 08:05) but inside window.
    clock = FakeClock(WINDOW_START + timedelta(minutes=6))
    async with stub.client() as http:
        worker = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock)
        await worker.poll_once()
    assert len(channel.sent_notifications) == 1
    assert channel.sent_notifications[0].template_key == "resumo_inicio_dia"
    confirm = [r for r in stub.requests if r.url.path == "/notificacoes/1/confirmacao-envio"]
    assert len(confirm) == 1
    assert b'"statusEnvio":"ENVIADA"' in confirm[0].content


async def test_before_send_time_does_not_send() -> None:
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    channel = FakeChannel()
    redis = FakeRedis()
    # chosen time is 08:05; poll at 08:02 must not send yet.
    clock = FakeClock(WINDOW_START + timedelta(minutes=2))
    async with stub.client() as http:
        worker = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock)
        await worker.poll_once()
    assert channel.sent_notifications == []


async def test_restart_keeps_chosen_send_time() -> None:
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    channel = FakeChannel()
    redis = FakeRedis()
    clock = FakeClock(WINDOW_START + timedelta(minutes=1))

    async with stub.client() as http:
        # first worker sees the item, picks 08:05, but it is not due yet.
        first = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock, fraction=0.5)
        await first.poll_once()
        stored = await DeliveryState(redis).get_send_time(1)
        assert stored == WINDOW_START + timedelta(minutes=5)

        # restart: a fresh worker with a different random must keep the stored time.
        second = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock, fraction=0.1)
        await second.poll_once()
        assert await DeliveryState(redis).get_send_time(1) == WINDOW_START + timedelta(minutes=5)
    assert channel.sent_notifications == []


async def test_concurrent_workers_send_once() -> None:
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    clock = FakeClock(WINDOW_START + timedelta(minutes=6))
    async with stub.client() as http:
        core = CoreClient(http)
        a = _worker(core=core, channel=channel, redis=redis, clock=clock)
        b = _worker(core=core, channel=channel, redis=redis, clock=clock)
        await asyncio.gather(a.poll_once(), b.poll_once())
    assert len(channel.sent_notifications) == 1


async def test_window_closed_marks_falha_and_alerts() -> None:
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    alerts = RecordingAlerts()
    # recover after the window ended, never sent.
    clock = FakeClock(WINDOW_END + timedelta(minutes=5))
    async with stub.client() as http:
        worker = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock, alerts=alerts)
        await worker.poll_once()
    assert channel.sent_notifications == []
    confirm = [r for r in stub.requests if r.url.path == "/notificacoes/1/confirmacao-envio"]
    assert len(confirm) == 1
    assert b'"statusEnvio":"FALHA"' in confirm[0].content
    assert len(alerts.mensagens) == 1


async def test_late_delivery_window_still_open_sends_on_recovery() -> None:
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    # chosen time 08:05 missed while down; recover at 08:09, window still open.
    clock = FakeClock(WINDOW_START + timedelta(minutes=9))
    async with stub.client() as http:
        worker = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock)
        await worker.poll_once()
    assert len(channel.sent_notifications) == 1


async def test_immediate_alert_latency_under_a_minute() -> None:
    # Immediate alerts get a [now, now+2min] window. With a 30 s poll,
    # one poll near window start sends within about a minute.
    now = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
    item = _agenda_item(
        notification_id=2,
        inicio=now,
        fim=now + timedelta(minutes=2),
        template_key="alerta_operacional",
    )
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [item])
    stub.json("POST", "/notificacoes/2/confirmacao-envio", 200, {"idNotificacao": 2})
    channel = FakeChannel()
    redis = FakeRedis()
    settings = DeliverySettings(poll_interval_s=30, confirm_base_s=0, confirm_cap_s=0)
    # chosen time is at the very start of the 2-min window (fraction 0).
    clock = FakeClock(now + timedelta(seconds=30))
    async with stub.client() as http:
        worker = DeliveryWorker(
            core=CoreClient(http),
            channel=channel,
            state=DeliveryState(redis),
            recipient=RECIPIENT,
            settings=settings,
            clock=clock,
            random_source=FixedRandom(0.0),
            alerts=None,
        )
        await worker.poll_once()
    assert len(channel.sent_notifications) == 1
    chosen = await DeliveryState(redis).get_send_time(2)
    assert chosen == now  # within one minute of window start
    assert settings.poll_interval_s <= 30


async def test_core_down_then_recovery_without_resending() -> None:
    # The core is down when the send fires. The agenda comes back only on
    # recovery; the message is sent once and confirmed with retry.
    channel = FakeChannel()
    redis = FakeRedis()
    clock = FakeClock(WINDOW_START + timedelta(minutes=6))

    agenda_up = {"down": False}

    def agenda_handler(request: httpx.Request) -> httpx.Response:
        if agenda_up["down"]:
            raise httpx.ConnectTimeout("core down", request=request)
        return httpx.Response(200, json=[_agenda_item()])

    confirm_calls = {"n": 0}

    def confirm_handler(_request: httpx.Request) -> httpx.Response:
        confirm_calls["n"] += 1
        if confirm_calls["n"] == 1:
            return httpx.Response(503)  # core still flaky on first confirm
        return httpx.Response(200, json={"idNotificacao": 1})

    stub = CoreStub()
    stub.on("GET", "/notificacoes/agenda", agenda_handler)
    stub.on("POST", "/notificacoes/1/confirmacao-envio", confirm_handler)

    async with stub.client() as http:
        worker = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock)

        # core down at the scheduled time: poll finds nothing to deliver.
        agenda_up["down"] = True
        await worker.poll_once()
        assert channel.sent_notifications == []

        # recovery, window still open: send once; confirm retried past the 503.
        agenda_up["down"] = False
        await worker.poll_once()
    assert len(channel.sent_notifications) == 1
    assert confirm_calls["n"] >= 2


async def test_confirmation_failure_then_recovery_does_not_resend() -> None:
    channel = FakeChannel()
    redis = FakeRedis()
    clock = FakeClock(WINDOW_START + timedelta(minutes=6))

    confirm_calls = {"n": 0}

    def confirm_handler(_request: httpx.Request) -> httpx.Response:
        confirm_calls["n"] += 1
        # core down for every confirm in the first poll (max_tentativas=3).
        if confirm_calls["n"] <= 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"idNotificacao": 1})

    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.on("POST", "/notificacoes/1/confirmacao-envio", confirm_handler)

    async with stub.client() as http:
        worker = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock)

        # first poll: send succeeds, confirmation exhausts retries (core down).
        await worker.poll_once()
        assert len(channel.sent_notifications) == 1
        stored = await DeliveryState(redis).sent_message_id(1)
        assert stored == channel.sent_notifications[0].message_id

        # second poll: core still lists it pending; confirm again, never resend.
        await worker.poll_once()
    assert len(channel.sent_notifications) == 1
    assert confirm_calls["n"] >= 4


async def test_start_runs_a_poll_then_stop_is_clean() -> None:
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    clock = FakeClock(WINDOW_START + timedelta(minutes=6))
    settings = DeliverySettings(poll_interval_s=60, confirm_base_s=0, confirm_cap_s=0)
    async with stub.client() as http:
        worker = DeliveryWorker(
            core=CoreClient(http),
            channel=channel,
            state=DeliveryState(redis),
            recipient=RECIPIENT,
            settings=settings,
            clock=clock,
            random_source=FixedRandom(0.5),
            alerts=None,
        )
        await worker.start()
        # yield control so the background task runs its first poll.
        for _ in range(20):
            await asyncio.sleep(0)
            if channel.sent_notifications:
                break
        await worker.stop()
    assert len(channel.sent_notifications) == 1


async def test_naive_agenda_window_sends_without_type_error() -> None:
    # Regression: the core serializes janelaInicio/janelaFim as a LocalDateTime
    # (UTC, no offset), so they arrive naive while the clock is tz-aware UTC.
    # Before the fix the naive/aware comparison raised TypeError and nothing was
    # ever delivered. The worker must normalize the window and send.
    naive_start = datetime(2026, 6, 15, 8, 0)  # no tzinfo, as the core sends it
    naive_end = datetime(2026, 6, 15, 8, 10)
    stub = CoreStub()
    stub.json(
        "GET",
        "/notificacoes/agenda",
        200,
        [_agenda_item(inicio=naive_start, fim=naive_end)],
    )
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    clock = FakeClock(datetime(2026, 6, 15, 8, 6, tzinfo=timezone.utc))
    async with stub.client() as http:
        worker = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock)
        await worker.poll_once()
    assert len(channel.sent_notifications) == 1
    confirm = [r for r in stub.requests if r.url.path == "/notificacoes/1/confirmacao-envio"]
    assert len(confirm) == 1
    assert b'"statusEnvio":"ENVIADA"' in confirm[0].content


async def test_factory_builds_worker_with_defaults() -> None:
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [])
    channel = FakeChannel()
    redis = FakeRedis()
    async with stub.client() as http:
        worker = create_delivery_worker(
            core=CoreClient(http),
            channel=channel,
            redis=redis,
            recipient=RECIPIENT,
            settings=_settings(),
            clock=FakeClock(WINDOW_START),
            random_source=FixedRandom(0.5),
        )
        await worker.poll_once()
    assert channel.sent_notifications == []


async def test_crashed_claim_without_message_id_past_ttl_reports_falha_and_alerts() -> None:
    # A send was claimed but the process died before the channel id was recorded
    # (guard holds the claim marker, no id). On recovery past the claim TTL the
    # outcome is unknown: report FALHA and alert rather than risk a resend.
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    alerts = RecordingAlerts()
    settings = DeliverySettings(
        confirm_base_s=0, confirm_cap_s=0, confirm_max_tentativas=3, claim_ttl_s=120
    )
    # the dead worker claimed at 08:01 and never recorded an id.
    claim_time = WINDOW_START + timedelta(minutes=1)
    await DeliveryState(redis).claim_send(1, claim_time.timestamp())
    # a fresh worker recovers at 08:09, inside the window but well past the TTL.
    clock = FakeClock(WINDOW_START + timedelta(minutes=9))
    async with stub.client() as http:
        worker = DeliveryWorker(
            core=CoreClient(http),
            channel=channel,
            state=DeliveryState(redis),
            recipient=RECIPIENT,
            settings=settings,
            clock=clock,
            random_source=FixedRandom(0.5),
            alerts=alerts,
        )
        await worker.poll_once()
    # the ambiguous item was never resent.
    assert channel.sent_notifications == []
    confirm = [r for r in stub.requests if r.url.path == "/notificacoes/1/confirmacao-envio"]
    assert len(confirm) == 1
    assert b'"statusEnvio":"FALHA"' in confirm[0].content
    assert len(alerts.mensagens) == 1


async def test_fresh_claim_without_message_id_waits_without_resending() -> None:
    # Within the claim TTL a send may still be in flight, so the worker must not
    # resend, confirm or alert; it waits for a later poll.
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    alerts = RecordingAlerts()
    settings = DeliverySettings(
        confirm_base_s=0, confirm_cap_s=0, confirm_max_tentativas=3, claim_ttl_s=120
    )
    claim_time = WINDOW_START + timedelta(minutes=6)
    await DeliveryState(redis).claim_send(1, claim_time.timestamp())
    # recover only 30 s later: the claim is still fresh.
    clock = FakeClock(claim_time + timedelta(seconds=30))
    async with stub.client() as http:
        worker = DeliveryWorker(
            core=CoreClient(http),
            channel=channel,
            state=DeliveryState(redis),
            recipient=RECIPIENT,
            settings=settings,
            clock=clock,
            random_source=FixedRandom(0.5),
            alerts=alerts,
        )
        await worker.poll_once()
    assert channel.sent_notifications == []
    assert [r for r in stub.requests if r.url.path == "/notificacoes/1/confirmacao-envio"] == []
    assert alerts.mensagens == []


async def test_send_records_message_id_under_the_claim() -> None:
    # After the channel returns an id, mark_sent stores it under the claim so a
    # later poll confirms without resending (the id replaces the claim marker).
    stub = CoreStub()
    stub.json("GET", "/notificacoes/agenda", 200, [_agenda_item()])
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    channel = FakeChannel()
    redis = FakeRedis()
    clock = FakeClock(WINDOW_START + timedelta(minutes=6))
    async with stub.client() as http:
        worker = _worker(core=CoreClient(http), channel=channel, redis=redis, clock=clock)
        await worker.poll_once()
    stored = await DeliveryState(redis).sent_message_id(1)
    assert stored == channel.sent_notifications[0].message_id
