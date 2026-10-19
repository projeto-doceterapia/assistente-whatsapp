"""The retry coordinator wiring the handler receives.

`main._build_handler` passes the same `RetryCoordinator` that drives the retry
worker into `InboundHandler`. These tests lock the contract that wiring enables:
with the coordinator, a retryable recording failure defers the final message to
the worker (the conversation stays in RECORDING); without it, the final message
is sent right away. Both run the real `InboundHandler._record` over fakes.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.channel.fake import FakeChannel
from app.conversation import State
from app.core.client import CoreClient
from app.core.models import OrigemMovimentacao, UnidadeMedida
from app.core.stub import CoreStub
from app.flow.handler import InboundHandler
from app.flow.models import Draft, DraftItem, FlowDeps
from app.llm.fake import FakeLlmProvider
from app.llm.schemas import AcaoItem
from app.purchase.extracao import ExtracaoService
from app.purchase.rendering import Categoria
from app.retry.coordinator import RetryCoordinator
from app.retry.settings import RetrySettings
from app.retry.state import RetryState
from tests.fake_redis import FakeRedis
from tests.flow_fakes import FakeState
from tests.test_retry_worker import FakeClock, FixedRandom

pytestmark = pytest.mark.asyncio

BENEFICIARIA = "5511999999999"


def _draft() -> Draft:
    item = DraftItem(
        index=0,
        acao=AcaoItem.ATUALIZAR,
        insumo_id=1,
        nome="Arroz",
        unidade=UnidadeMedida.KG,
        quantidade_total=Decimal(10),
        calculo="10 kg",
    )
    return Draft(
        fluxo_id="fluxo-wire",
        chave_acesso=None,
        origem=OrigemMovimentacao.TEXTO,
        itens=[item],
    )


def _deps(http: httpx.AsyncClient, state: FakeState, channel: FakeChannel) -> FlowDeps:
    core = CoreClient(http)
    llm = FakeLlmProvider([])

    async def categorias() -> list[Categoria]:
        return []

    return FlowDeps(
        state=state,
        channel=channel,
        core=core,
        llm=llm,
        classifier=_NeverClassifier(),
        extracao=ExtracaoService(llm, core),
        beneficiaria=BENEFICIARIA,
        categorias_provider=categorias,
        max_ajustes=2,
    )


class _NeverClassifier:
    async def classify(self, text: str):  # pragma: no cover - never reached here
        raise AssertionError("classify must not be called by _record")


def _coordinator(redis: FakeRedis, clock: FakeClock) -> RetryCoordinator:
    return RetryCoordinator(
        state=RetryState(redis),
        recipient=BENEFICIARIA,
        settings=RetrySettings(
            base_s=2, factor=2, cap_s=300, max_tentativas=3, request_window_s=3600
        ),
        clock=clock,
        random_source=FixedRandom(1.0),
    )


async def test_retryable_failure_defers_final_message_when_coordinator_wired() -> None:
    stub = CoreStub()
    stub.timeout("POST", "/estoque/movimentos")  # transient: item is retryable
    channel = FakeChannel()
    state = FakeState()
    redis = FakeRedis()
    async with stub.client() as http:
        coordinator = _coordinator(redis, FakeClock())
        handler = InboundHandler(_deps(http, state, channel), coordinator)
        await handler._record(BENEFICIARIA, _draft())  # noqa: SLF001 - unit under test

    # Only the "recording_wait" ack was sent; the final message is the worker's.
    assert all("Pronto" not in t.text for t in channel.sent_texts)
    assert all("Não consegui" not in t.text for t in channel.sent_texts)
    # The conversation is held in RECORDING for the worker to finish.
    stored = await state.load_conversation(BENEFICIARIA)
    assert stored["estado"] == State.RECORDING.value


async def test_retryable_failure_sends_final_immediately_without_coordinator() -> None:
    stub = CoreStub()
    stub.timeout("POST", "/estoque/movimentos")
    channel = FakeChannel()
    state = FakeState()
    async with stub.client() as http:
        handler = InboundHandler(_deps(http, state, channel))  # no coordinator
        await handler._record(BENEFICIARIA, _draft())  # noqa: SLF001 - unit under test

    # Without the worker, the final message is sent right away (a single
    # retryable item that failed is reported as a failure) and the conversation
    # is cleared.
    assert any("Não consegui" in t.text for t in channel.sent_texts)
    assert await state.load_conversation(BENEFICIARIA) == {}
