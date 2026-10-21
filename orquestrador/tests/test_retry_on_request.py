"""On-request retry end-to-end on the fake channel, core stub,
fake LLM and fake Redis.

Each test maps to a scenario of the "Retry of failed items on request"
requirement: retry after the core comes back, a reactivated supply, a
not-retryable failure (no offer), the window expiring, and a new purchase
dropping the offer. The owner's conversation runs through the real
`InboundHandler`; the retry worker drains the queue between messages.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.channel.fake import FakeChannel
from app.channel.port import InboundMessage, MessageKind
from app.core.client import CoreClient
from app.core.stub import CoreStub
from app.flow.handler import InboundHandler
from app.flow.intent import ReplyIntent
from app.flow.models import FlowDeps
from app.llm.fake import FakeLlmProvider
from app.purchase.extracao import ExtracaoService
from app.purchase.rendering import Categoria
from app.recording import Recorder
from app.retry.coordinator import RetryCoordinator
from app.retry.settings import RetrySettings
from app.retry.state import RetryState
from app.retry.worker import RetryWorker
from tests.fake_redis import FakeRedis
from tests.flow_fakes import FakeState
from tests.test_retry_worker import FakeClock, FixedRandom

pytestmark = pytest.mark.asyncio

BENEFICIARIA = "5511999999999"
CATEGORIAS = [Categoria(idCategoriaInsumo=1, nome="Farinhas")]


class FakeClassifier:
    def __init__(self) -> None:
        self._intents: list[ReplyIntent] = []
        self.calls: list[str] = []

    def queue(self, intent: ReplyIntent) -> None:
        self._intents.append(intent)

    async def classify(self, text: str) -> ReplyIntent:
        self.calls.append(text)
        return self._intents.pop(0) if self._intents else ReplyIntent.OTHER


_CATALOGO = {
    "itens": [
        {"idInsumo": 1, "nome": "Arroz", "unidade": "KG"},
        {"idInsumo": 2, "nome": "Feijão", "unidade": "KG"},
    ],
    "total": 2,
    "proximoCursor": None,
}


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        201,
        json={
            "idMovimentacao": 1,
            "insumoId": 1,
            "tipo": "ENTRADA_COMPRA",
            "quantidade": "10",
            "saldoAtual": "10",
            "duplicado": False,
        },
    )


def _item_json(*, insumo_id=1, nome="Arroz", qtd="2", tam="5", un="KG") -> str:
    return (
        f'{{"acao":"ATUALIZAR","insumo_id":{insumo_id},"nome_extraido":"{nome}",'
        f'"quantidade_embalagens":{qtd},"tamanho_embalagem":{tam},'
        f'"unidade_embalagem":"{un}","unidade":"{un}","termos_busca":[]}}'
    )


def _extracao_json(itens: list[str]) -> str:
    return f'{{"itens":[{",".join(itens)}],"chave_acesso":null}}'


class Harness:
    """Full handler + retry worker over shared fakes."""

    def __init__(self, http: httpx.AsyncClient, llm: FakeLlmProvider, clock: FakeClock) -> None:
        self.channel = FakeChannel()
        self.state = FakeState()
        self.classifier = FakeClassifier()
        self.clock = clock
        self.redis = FakeRedis()
        core = CoreClient(http)
        settings = RetrySettings(
            base_s=2, factor=2, cap_s=300, max_tentativas=3, request_window_s=3600
        )
        self.coordinator = RetryCoordinator(
            state=RetryState(self.redis),
            recipient=BENEFICIARIA,
            settings=settings,
            clock=clock,
            random_source=FixedRandom(1.0),
        )
        self.worker = RetryWorker(
            state=RetryState(self.redis),
            conversation=self.state,
            channel=self.channel,
            replay=Recorder(self.state, core).record,
            coordinator=self.coordinator,
            settings=settings,
            clock=clock,
            random_source=FixedRandom(1.0),
        )
        deps = FlowDeps(
            state=self.state,
            channel=self.channel,
            core=core,
            llm=llm,
            classifier=self.classifier,
            extracao=ExtracaoService(llm, core),
            beneficiaria=BENEFICIARIA,
            categorias_provider=self._categorias,
            max_ajustes=2,
        )
        self.handler = InboundHandler(deps, self.coordinator)

    async def _categorias(self) -> list[Categoria]:
        return CATEGORIAS

    @property
    def last(self) -> str:
        return self.channel.sent_texts[-1].text

    @property
    def texts(self) -> list[str]:
        return [t.text for t in self.channel.sent_texts]

    async def text(self, text: str, mid: str) -> None:
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.TEXT,
                timestamp=datetime.now(tz=timezone.utc),
                text=text,
            )
        )

    async def drain(self, steps: int = 6) -> None:
        for _ in range(steps):
            self.clock.advance(300)
            await self.worker.poll_once()


def _stub(movimento_handler) -> CoreStub:
    stub = CoreStub()
    stub.json("GET", "/insumos/catalogo", 200, _CATALOGO)
    stub.on("POST", "/estoque/movimentos", movimento_handler)
    return stub


# --- Scenario: Retry after the core comes back ------------------------------


async def test_retry_after_core_recovers() -> None:
    down = {"on": True}

    def movimento(request: httpx.Request) -> httpx.Response:
        if down["on"]:
            raise httpx.ConnectTimeout("core down", request=request)
        return _ok(request)

    llm = FakeLlmProvider([_extracao_json([_item_json(nome="Arroz")])])
    clock = FakeClock()
    async with _stub(movimento).client() as http:
        h = Harness(http, llm, clock)
        await h.text("comprei 2 x 5 kg de arroz", "m1")
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.text("sim", "m2")
        # recording failed (core down), item is retrying: no final message yet.
        assert all("Pronto" not in t for t in h.texts)

        # attempts exhaust while the core stays down -> partial with the offer.
        await h.drain()
        assert "tenta de novo" in h.last

        # the core comes back; the owner asks to retry.
        down["on"] = False
        h.classifier.queue(ReplyIntent.RETRY)
        await h.text("tenta de novo", "m3")
        assert "Vou tentar registrar de novo" in h.last
        await h.drain()

    # the item was recorded on the on-request retry, same key.
    assert "Pronto! Atualizei seu estoque" in h.last


# --- Scenario: Supply reactivated (404 then available) ----------------------


async def test_retry_after_supply_reactivated() -> None:
    state = {"missing": True}

    def movimento(request: httpx.Request) -> httpx.Response:
        if state["missing"]:
            return httpx.Response(404)
        return _ok(request)

    llm = FakeLlmProvider([_extracao_json([_item_json(nome="Arroz")])])
    clock = FakeClock()
    async with _stub(movimento).client() as http:
        h = Harness(http, llm, clock)
        await h.text("comprei arroz", "m1")
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.text("sim", "m2")
        await h.drain()
        assert "tenta de novo" in h.last

        # the owner reactivates the supply in the web app, then asks to retry.
        state["missing"] = False
        h.classifier.queue(ReplyIntent.RETRY)
        await h.text("tenta de novo", "m3")
        await h.drain()

    assert "Pronto! Atualizei seu estoque" in h.last


# --- Scenario: Not retryable (definitive failure, no offer) -----------------


async def test_definitive_failure_offers_no_retry() -> None:
    def movimento(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422)  # validation: definitive

    llm = FakeLlmProvider([_extracao_json([_item_json(nome="Arroz")])])
    clock = FakeClock()
    async with _stub(movimento).client() as http:
        h = Harness(http, llm, clock)
        await h.text("comprei arroz", "m1")
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.text("sim", "m2")

        # a definitive failure is reported immediately, with no retry offer.
        assert "tenta de novo" not in h.last
        assert "Tente de novo mais tarde" in h.last
        # no retry window was opened.
        assert await h.coordinator.request_status(BENEFICIARIA) == "NONE"


# --- Scenario: Retry window expired -----------------------------------------


async def test_retry_window_expired() -> None:
    def movimento(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    llm = FakeLlmProvider([_extracao_json([_item_json(nome="Arroz")])])
    clock = FakeClock()
    async with _stub(movimento).client() as http:
        h = Harness(http, llm, clock)
        await h.text("comprei arroz", "m1")
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.text("sim", "m2")
        await h.drain()
        assert "tenta de novo" in h.last

        # the window (3600 s) passes before she asks.
        clock.advance(4000)
        h.classifier.queue(ReplyIntent.RETRY)
        await h.text("tenta de novo", "m3")

    assert "já expiraram" in h.last


# --- Scenario: New purchase instead of retry --------------------------------


async def test_new_purchase_drops_the_offer() -> None:
    down = {"on": True}

    def movimento(request: httpx.Request) -> httpx.Response:
        if down["on"]:
            raise httpx.ConnectTimeout("down", request=request)
        return _ok(request)

    llm = FakeLlmProvider(
        [
            _extracao_json([_item_json(nome="Arroz")]),
            _extracao_json([_item_json(nome="Feijão", insumo_id=2)]),
        ]
    )
    clock = FakeClock()
    async with _stub(movimento).client() as http:
        h = Harness(http, llm, clock)
        await h.text("comprei arroz", "m1")
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.text("sim", "m2")
        await h.drain()
        assert "tenta de novo" in h.last
        assert await h.coordinator.request_status(BENEFICIARIA) == "ACTIVE"

        # a new purchase arrives while the offer is live: it drops the offer and
        # a fresh reading starts (NEW_PURCHASE intent, not RETRY).
        down["on"] = False
        h.classifier.queue(ReplyIntent.NEW_PURCHASE)
        await h.text("comprei feijão", "m3")

    assert "Entendi esta compra" in h.last
    assert await h.coordinator.request_status(BENEFICIARIA) == "NONE"
