"""Retry worker, state and backoff tests on the fake channel, core stub and fake
Redis. No network, no real Redis.

They assert the retry guarantees: a full-jitter exponential backoff schedule
with a fixed random source, the DLQ after the attempts are exhausted, no double
send under two workers, and survival of a process kill/restart during recording
simulated by driving a fresh worker over the same fake Redis.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx

from app.channel.fake import FakeChannel
from app.core.client import CoreClient
from app.core.models import OrigemMovimentacao, UnidadeMedida
from app.core.stub import CoreStub
from app.flow.models import Draft, DraftItem
from app.llm.schemas import AcaoItem
from app.recording import Recorder
from app.retry.backoff import full_jitter_delay
from app.retry.coordinator import RetryCoordinator
from app.retry.settings import RetrySettings
from app.retry.state import ALREADY_COMPLETED, RetryState
from app.retry.worker import RetryWorker
from tests.fake_redis import FakeRedis
from tests.flow_fakes import FakeState

RECIPIENT = "5511999999999"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self._now = now

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def set(self, now: float) -> None:
        self._now = now


class FixedRandom:
    """Returns a fixed fraction of the span, so the backoff delay is known."""

    def __init__(self, fraction: float = 1.0) -> None:
        self._fraction = fraction

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self._fraction


class RecordingAlerts:
    def __init__(self) -> None:
        self.mensagens: list[str] = []

    async def raise_alert(self, mensagem: str) -> None:
        self.mensagens.append(mensagem)


def _settings(**overrides) -> RetrySettings:
    base = dict(base_s=2, factor=2, cap_s=300, max_tentativas=6, request_window_s=24 * 3600)
    base.update(overrides)
    return RetrySettings(**base)


def _item(index: int, *, insumo_id=10, nome="Arroz") -> DraftItem:
    return DraftItem(
        index=index,
        acao=AcaoItem.ATUALIZAR,
        insumo_id=insumo_id,
        nome=nome,
        unidade=UnidadeMedida.KG,
        quantidade_total=Decimal(10),
        calculo="10 kg",
    )


def _draft(itens, *, fluxo="fluxo-1") -> Draft:
    return Draft(
        fluxo_id=fluxo,
        chave_acesso=None,
        origem=OrigemMovimentacao.TEXTO,
        itens=itens,
    )


def _movimento_201(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        201,
        json={
            "idMovimentacao": 1,
            "insumoId": 10,
            "tipo": "ENTRADA_COMPRA",
            "quantidade": "10",
            "saldoAtual": "10",
            "duplicado": False,
        },
    )


def _build(http, redis, clock, channel, *, settings=None, random_source=None, alerts=None):
    """Wire a coordinator (recording sink) and a worker over shared fakes."""
    state = RetryState(redis)
    settings = settings or _settings()
    random_source = random_source or FixedRandom(1.0)
    coordinator = RetryCoordinator(
        state=state,
        recipient=RECIPIENT,
        settings=settings,
        clock=clock,
        random_source=random_source,
    )
    conversation = FakeState()
    # The sink recorder (first recording) uses the coordinator; the replay
    # recorder the worker drives uses the default no-op sink so a failed replay
    # never re-enqueues itself (the worker owns rescheduling).
    sink_recorder = Recorder(conversation, CoreClient(http), coordinator.enqueue)
    replay_recorder = Recorder(conversation, CoreClient(http))
    worker = RetryWorker(
        state=state,
        conversation=conversation,
        channel=channel,
        replay=replay_recorder.record,
        coordinator=coordinator,
        settings=settings,
        clock=clock,
        random_source=random_source,
        alerts=alerts,
    )
    return coordinator, worker, sink_recorder, state, conversation


# --- backoff schedule -------------------------------------------------------


def test_full_jitter_schedule_with_full_fraction() -> None:
    s = _settings()
    rnd = FixedRandom(1.0)  # draw the whole ceiling: deterministic schedule
    delays = [full_jitter_delay(attempt, s, rnd) for attempt in range(1, 9)]
    # base * factor**(attempt-1) capped at 300: 2,4,8,16,32,64,128,256->... capped
    assert delays[:6] == [2, 4, 8, 16, 32, 64]
    # attempt 9 would be 512 but the cap pins it to 300.
    assert full_jitter_delay(9, s, rnd) == 300


def test_full_jitter_stays_within_bounds() -> None:
    s = _settings()
    half = full_jitter_delay(3, s, FixedRandom(0.5))
    assert half == 4  # half of the 8 s ceiling for attempt 3


# --- a transient failure is retried and then succeeds -----------------------


async def test_transient_item_retried_then_succeeds() -> None:
    calls = {"n": 0}

    def movimento(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("core down", request=request)
        return _movimento_201(request)

    stub = CoreStub()
    stub.on("POST", "/estoque/movimentos", movimento)
    redis = FakeRedis()
    clock = FakeClock()
    channel = FakeChannel()
    async with stub.client() as http:
        coord, worker, sink_recorder, state, conv = _build(http, redis, clock, channel)
        result = await sink_recorder.record(_draft([_item(0)]))
        assert result.has_retryable
        final = await coord.finalize(_draft([_item(0)]), result)
        assert final is None  # deferred to the worker

        # not due yet (scheduled 2 s out); nothing happens.
        await worker.poll_once()
        assert channel.sent_texts == []

        clock.advance(2)
        await worker.poll_once()

    assert len(channel.sent_texts) == 1
    assert "Pronto! Atualizei seu estoque" in channel.sent_texts[0].text


# --- DLQ after the attempts are exhausted -----------------------------------


async def test_dlq_after_max_attempts_and_alert() -> None:
    stub = CoreStub()
    stub.timeout("POST", "/estoque/movimentos")  # always down
    redis = FakeRedis()
    clock = FakeClock()
    channel = FakeChannel()
    alerts = RecordingAlerts()
    async with stub.client() as http:
        coord, worker, sink_recorder, state, conv = _build(
            http, redis, clock, channel, settings=_settings(max_tentativas=3), alerts=alerts
        )
        result = await sink_recorder.record(_draft([_item(0)]))
        await coord.finalize(_draft([_item(0)]), result)
        # attempt 1 was the recording; drive retries 2 and 3 (exhaust at 3).
        for _ in range(5):
            clock.advance(300)
            await worker.poll_once()

    assert await state.dlq_size() == 1
    assert len(alerts.mensagens) == 1
    # the final message was sent once, reporting the failure.
    assert len(channel.sent_texts) == 1
    assert "Não consegui registrar" in channel.sent_texts[0].text


# --- no double send under two concurrent workers ----------------------------


async def test_two_workers_send_item_once() -> None:
    stub = CoreStub()
    stub.on("POST", "/estoque/movimentos", _movimento_201)
    redis = FakeRedis()
    clock = FakeClock()
    channel = FakeChannel()
    async with stub.client() as http:
        coord, worker_a, sink_recorder, state, conv = _build(http, redis, clock, channel)
        # second worker over the same Redis/channel.
        _, worker_b, _, _, _ = _build(http, redis, clock, channel)
        # seed a failed item scheduled now.
        timeout_stub = CoreStub()
        timeout_stub.timeout("POST", "/estoque/movimentos")
        async with timeout_stub.client() as down_http:
            down_recorder = Recorder(conv, CoreClient(down_http), coord.enqueue)
            result = await down_recorder.record(_draft([_item(0)]))
            await coord.finalize(_draft([_item(0)]), result)

        clock.advance(2)
        # both poll at once: the NX claim must let only one resend.
        before = len(stub.requests)
        await asyncio.gather(worker_a.poll_once(), worker_b.poll_once())
        resends = len(stub.requests) - before

    assert resends == 1
    assert len(channel.sent_texts) == 1


# --- survive a kill/restart during recording --------------------------------


async def test_restart_during_recording_records_without_duplicates() -> None:
    # Recording enqueues a retry; the "process is killed" before the worker runs.
    # A fresh worker built over the same fake Redis resumes and records the item
    # exactly once, with the same idempotency key.
    down = CoreStub()
    down.timeout("POST", "/estoque/movimentos")
    redis = FakeRedis()
    clock = FakeClock()
    channel = FakeChannel()

    async with down.client() as http:
        coord, _worker, sink_recorder, state, conv = _build(http, redis, clock, channel)
        result = await sink_recorder.record(_draft([_item(0)]))
        await coord.finalize(_draft([_item(0)]), result)
    # the record and schedule survive only in Redis; the worker object is gone.

    up = CoreStub()
    up.on("POST", "/estoque/movimentos", _movimento_201)
    async with up.client() as http:
        # fresh worker + coordinator over the SAME redis and conversation.
        fresh_state = RetryState(redis)
        fresh_coord = RetryCoordinator(
            state=fresh_state,
            recipient=RECIPIENT,
            settings=_settings(),
            clock=clock,
            random_source=FixedRandom(1.0),
        )
        replay = Recorder(conv, CoreClient(http))
        fresh = RetryWorker(
            state=fresh_state,
            conversation=conv,
            channel=channel,
            replay=replay.record,
            coordinator=fresh_coord,
            settings=_settings(),
            clock=clock,
            random_source=FixedRandom(1.0),
        )
        clock.advance(2)
        await fresh.poll_once()
        resends = [r for r in up.requests if r.url.path == "/estoque/movimentos"]

    assert len(resends) == 1  # recorded exactly once after recovery
    assert resends[0].headers["Idempotency-Key"] == "fluxo:fluxo-1:10"
    assert len(channel.sent_texts) == 1
    assert "Pronto! Atualizei seu estoque" in channel.sent_texts[0].text


# --- completion is idempotent ----------------------------------------------


async def test_decrement_pending_is_idempotent_past_zero() -> None:
    # A single pending item: the first completion drives the counter to zero;
    # a second (crashed or duplicated) completion must be a no-op. Before the
    # fix a DECR on the already-deleted key resurrected it at -1; now the key
    # stays gone and the sentinel is returned.
    redis = FakeRedis()
    state = RetryState(redis)
    await state.set_pending("fluxo-1", 1)

    assert await state.decrement_pending("fluxo-1") == 0
    assert await state.decrement_pending("fluxo-1") == ALREADY_COMPLETED
    assert await state.decrement_pending("fluxo-1") == ALREADY_COMPLETED
    # The counter key was not resurrected by the extra decrements.
    assert await redis.get("retry:pending:fluxo-1") is None


async def test_double_completion_sends_final_message_once() -> None:
    # Two workers complete the same purchase's last item concurrently. The first
    # is suspended inside send_text (after it read the snapshot, before the
    # worker drops it), so the second runs its completion in the same window.
    # Before the fix the second worker's decrement resurrected the counter and
    # passed the 'last item' check, composing and sending the final message a
    # second time from the still-present snapshot. The idempotent counter makes
    # the second completion a no-op.
    stub = CoreStub()
    stub.on("POST", "/estoque/movimentos", _movimento_201)
    redis = FakeRedis()
    clock = FakeClock()
    gate = asyncio.Event()
    channel = _GatedChannel(gate)
    async with stub.client() as http:
        coord, worker_a, sink_recorder, state, conv = _build(http, redis, clock, channel)
        _, worker_b, _, _, _ = _build(http, redis, clock, channel)
        timeout_stub = CoreStub()
        timeout_stub.timeout("POST", "/estoque/movimentos")
        async with timeout_stub.client() as down_http:
            down_recorder = Recorder(conv, CoreClient(down_http), coord.enqueue)
            result = await down_recorder.record(_draft([_item(0)]))
            await coord.finalize(_draft([_item(0)]), result)
        await state.resolve_member(
            "fluxo-1", "fluxo-1:item:0", {"status": "OK", "nome": "Arroz", "calculo": "10 kg"}
        )

        # worker A enters completion and blocks inside send_text.
        first = asyncio.create_task(worker_a._complete_if_last("fluxo-1"))
        while not channel.sending:
            await asyncio.sleep(0)
        # worker B tries to complete the same purchase in that window.
        await worker_b._complete_if_last("fluxo-1")
        # release A.
        gate.set()
        await first

    assert len(channel.sent_texts) == 1


class _GatedChannel(FakeChannel):
    """A channel whose first send_text blocks on a gate, so a second completion
    runs while the first is mid-send."""

    def __init__(self, gate: asyncio.Event) -> None:
        super().__init__()
        self._gate = gate
        self.sending = False
        self._blocked_once = False

    async def send_text(self, recipient: str, text: str):
        if not self._blocked_once:
            self._blocked_once = True
            self.sending = True
            await self._gate.wait()
        return await super().send_text(recipient, text)
