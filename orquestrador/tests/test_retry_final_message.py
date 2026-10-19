"""Final result message tests: the worker sends exactly one final
message when a purchase's last retryable item resolves, with the summary first,
in the success / partial / failure shapes of the catalog.

The message is reconstructed by the coordinator from the per-item outcomes, so
these also lock the DLQ-vs-recorded wording.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from app.channel.fake import FakeChannel
from app.core.client import CoreClient
from app.core.models import OrigemMovimentacao, UnidadeMedida
from app.core.stub import CoreStub
from app.flow.models import Draft, DraftItem
from app.llm.schemas import AcaoItem
from app.recording import Recorder
from app.retry.coordinator import RetryCoordinator
from app.retry.settings import RetrySettings
from app.retry.state import RetryState
from app.retry.worker import RetryWorker
from tests.fake_redis import FakeRedis
from tests.flow_fakes import FakeState
from tests.test_retry_worker import FakeClock, FixedRandom, RECIPIENT


def _settings(**overrides) -> RetrySettings:
    base = dict(base_s=2, factor=2, cap_s=300, max_tentativas=3, request_window_s=24 * 3600)
    base.update(overrides)
    return RetrySettings(**base)


def _item(index: int, *, insumo_id: int, nome: str, calc: str) -> DraftItem:
    return DraftItem(
        index=index,
        acao=AcaoItem.ATUALIZAR,
        insumo_id=insumo_id,
        nome=nome,
        unidade=UnidadeMedida.KG,
        quantidade_total=Decimal(10),
        calculo=calc,
    )


def _draft(itens, fluxo="fluxo-final") -> Draft:
    return Draft(fluxo_id=fluxo, chave_acesso=None, origem=OrigemMovimentacao.TEXTO, itens=itens)


def _ok(request: httpx.Request) -> httpx.Response:
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


def _wire(http_sink, http_replay, redis, clock, channel, settings):
    state = RetryState(redis)
    rnd = FixedRandom(1.0)
    coord = RetryCoordinator(
        state=state, recipient=RECIPIENT, settings=settings, clock=clock, random_source=rnd
    )
    conv = FakeState()
    sink_recorder = Recorder(conv, CoreClient(http_sink), coord.enqueue)
    worker = RetryWorker(
        state=state,
        conversation=conv,
        channel=channel,
        replay=Recorder(conv, CoreClient(http_replay)).record,
        coordinator=coord,
        settings=settings,
        clock=clock,
        random_source=rnd,
    )
    return coord, worker, sink_recorder, conv


async def _run_to_completion(worker, clock, steps=6) -> None:
    for _ in range(steps):
        clock.advance(300)
        await worker.poll_once()


# --- partial: one recorded, one retried and recovered -> all recorded -------


async def test_partial_recovers_sends_success_summary_first() -> None:
    itens = [
        _item(0, insumo_id=10, nome="Arroz", calc="10 kg"),
        _item(1, insumo_id=11, nome="Feijão", calc="3 kg"),
    ]
    # First recording: item 0 ok, item 1 times out (retryable).
    sink_calls = {"n": 0}

    def sink_mov(request: httpx.Request) -> httpx.Response:
        sink_calls["n"] += 1
        if sink_calls["n"] == 2:
            raise httpx.ConnectTimeout("down", request=request)
        return _ok(request)

    sink_stub = CoreStub()
    sink_stub.on("POST", "/estoque/movimentos", sink_mov)
    up = CoreStub()
    up.on("POST", "/estoque/movimentos", _ok)  # core is back for the retry

    redis = FakeRedis()
    clock = FakeClock()
    channel = FakeChannel()
    async with sink_stub.client() as hs, up.client() as hr:
        coord, worker, sink_recorder, conv = _wire(hs, hr, redis, clock, channel, _settings())
        result = await sink_recorder.record(_draft(itens))
        assert await coord.finalize(_draft(itens), result) is None
        await _run_to_completion(worker, clock)

    assert len(channel.sent_texts) == 1
    text = channel.sent_texts[0].text
    assert text.startswith("Pronto! Atualizei seu estoque")
    assert "Arroz" in text and "Feijão" in text
    # the conversation was cleared on completion.
    assert await conv.load_conversation(RECIPIENT) == {}


# --- partial: one recorded, one exhausted -> partial with retry offer -------


async def test_partial_exhausted_sends_partial_with_offer() -> None:
    itens = [
        _item(0, insumo_id=10, nome="Arroz", calc="10 kg"),
        _item(1, insumo_id=11, nome="Feijão", calc="3 kg"),
    ]
    sink_calls = {"n": 0}

    def sink_mov(request: httpx.Request) -> httpx.Response:
        sink_calls["n"] += 1
        if sink_calls["n"] == 2:
            raise httpx.ConnectTimeout("down", request=request)
        return _ok(request)

    sink_stub = CoreStub()
    sink_stub.on("POST", "/estoque/movimentos", sink_mov)
    down = CoreStub()
    down.timeout("POST", "/estoque/movimentos")  # stays down through exhaustion

    redis = FakeRedis()
    clock = FakeClock()
    channel = FakeChannel()
    async with sink_stub.client() as hs, down.client() as hr:
        coord, worker, sink_recorder, conv = _wire(hs, hr, redis, clock, channel, _settings())
        result = await sink_recorder.record(_draft(itens))
        await coord.finalize(_draft(itens), result)
        await _run_to_completion(worker, clock)

    assert len(channel.sent_texts) == 1
    text = channel.sent_texts[0].text
    assert text.startswith("Registrei estes itens")
    assert "Arroz" in text
    assert "Não consegui registrar" in text
    assert "Feijão" in text
    assert "tenta de novo" in text  # retry offer, item is retryable


# --- total failure: single item exhausted -> failure message ---------------


async def test_total_failure_single_item_sends_failure() -> None:
    itens = [_item(0, insumo_id=10, nome="Arroz", calc="10 kg")]
    sink_stub = CoreStub()
    sink_stub.timeout("POST", "/estoque/movimentos")
    down = CoreStub()
    down.timeout("POST", "/estoque/movimentos")

    redis = FakeRedis()
    clock = FakeClock()
    channel = FakeChannel()
    async with sink_stub.client() as hs, down.client() as hr:
        coord, worker, sink_recorder, conv = _wire(hs, hr, redis, clock, channel, _settings())
        result = await sink_recorder.record(_draft(itens))
        await coord.finalize(_draft(itens), result)
        await _run_to_completion(worker, clock)

    assert len(channel.sent_texts) == 1
    assert channel.sent_texts[0].text.startswith("Registrei estes itens") is False
    assert "Não consegui" in channel.sent_texts[0].text


# --- the final message is sent only once, when the LAST item finishes -------


async def test_final_sent_once_after_last_item() -> None:
    itens = [
        _item(0, insumo_id=10, nome="Arroz", calc="10 kg"),
        _item(1, insumo_id=11, nome="Feijão", calc="3 kg"),
    ]
    # Both items fail on first recording (retryable); the core comes back for
    # both retries, which land on different polls.
    sink_stub = CoreStub()
    sink_stub.timeout("POST", "/estoque/movimentos")
    up = CoreStub()
    up.on("POST", "/estoque/movimentos", _ok)

    redis = FakeRedis()
    clock = FakeClock()
    channel = FakeChannel()
    async with sink_stub.client() as hs, up.client() as hr:
        coord, worker, sink_recorder, conv = _wire(hs, hr, redis, clock, channel, _settings())
        result = await sink_recorder.record(_draft(itens))
        await coord.finalize(_draft(itens), result)
        clock.advance(2)
        await worker.poll_once()  # both due at +2 s, both recorded this poll

    assert len(channel.sent_texts) == 1
    assert channel.sent_texts[0].text.startswith("Pronto! Atualizei seu estoque")
