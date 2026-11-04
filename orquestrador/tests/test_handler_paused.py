"""The paused assistant: one reply, then silence, with nothing read or stored."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.channel.fake import FakeChannel
from app.channel.port import InboundMessage, MessageKind
from app.core.client import CoreClient
from app.core.stub import CoreStub
from app.core.switches import AssistantSwitches
from app.flow.handler import InboundHandler
from app.flow.intent import ReplyIntent
from app.flow.models import FlowDeps
from app.llm.fake import FakeLlmProvider
from app.purchase.extracao import ExtracaoService
from app.purchase.rendering import Categoria
from app.state import keys
from app.state.store import ConversationState
from tests.fake_redis import FakeRedis
from tests.flow_fakes import FakeState

pytestmark = pytest.mark.asyncio

BENEFICIARIA = "5511900000001"
PATH = "/configuracao-notificacao"
PAUSADO = "Assistente pausado. Reative em Configurações."
EXTRACAO_VAZIA = '{"itens":[],"chave_acesso":null}'


class FakeClassifier:
    async def classify(self, text: str) -> ReplyIntent:
        return ReplyIntent.OTHER


class FakeClock:
    def __init__(self) -> None:
        self.instante = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.instante


class CountingChannel(FakeChannel):
    def __init__(self) -> None:
        super().__init__()
        self.fetched: list[str] = []

    async def fetch_media(self, media_ref: str) -> bytes:
        self.fetched.append(media_ref)
        return await super().fetch_media(media_ref)


class Harness:
    def __init__(self, http: httpx.AsyncClient, *, wire_switches: bool = True) -> None:
        self.channel = CountingChannel()
        self.state = FakeState()
        self.llm = FakeLlmProvider([])
        core = CoreClient(http)
        deps = FlowDeps(
            state=self.state,
            channel=self.channel,
            core=core,
            llm=self.llm,
            classifier=FakeClassifier(),
            extracao=ExtracaoService(self.llm, core),
            beneficiaria=BENEFICIARIA,
            categorias_provider=self._categorias,
            max_ajustes=2,
        )
        switches = AssistantSwitches(core, FakeClock()) if wire_switches else None
        self.handler = InboundHandler(deps, switches=switches)
        self.categorias_lidas = 0

    async def _categorias(self) -> list[Categoria]:
        self.categorias_lidas += 1
        return [Categoria(idCategoriaInsumo=1, nome="Farinhas")]

    @property
    def texts(self) -> list[str]:
        return [sent.text for sent in self.channel.sent_texts]

    async def texto(self, mid: str) -> None:
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.TEXT,
                timestamp=datetime.now(tz=timezone.utc),
                text="comprei 2 kg de farinha",
            )
        )

    async def foto(self, mid: str) -> None:
        self.channel.stage_media("ref-1", b"imagem-ficticia")
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.IMAGE,
                timestamp=datetime.now(tz=timezone.utc),
                media_ref="ref-1",
            )
        )


def _stub(*, assistente: bool) -> CoreStub:
    stub = CoreStub()
    stub.json(
        "GET", PATH, 200, {"avisosAutomaticosAtivo": True, "assistenteAtivo": assistente}
    )
    return stub


async def test_single_paused_reply() -> None:
    stub = _stub(assistente=False)
    async with stub.client() as http:
        h = Harness(http)
        await h.texto("m1")
        await h.texto("m2")
        await h.foto("m3")
    assert h.texts == [PAUSADO]


async def test_paused_skips_invoice() -> None:
    stub = _stub(assistente=False)
    async with stub.client() as http:
        h = Harness(http)
        await h.foto("m1")
        await h.foto("m2")
    assert h.channel.fetched == []
    assert h.llm.requests == []
    assert h.categorias_lidas == 0
    assert h.state._conversations == {}
    assert [request.url.path for request in stub.requests] == [PATH]


async def test_paused_runs_before_the_lock() -> None:
    stub = _stub(assistente=False)
    async with stub.client() as http:
        h = Harness(http)
        await h.state.acquire_lock(BENEFICIARIA)
        await h.texto("m1")
    assert h.texts == [PAUSADO]


async def test_duplicate_while_paused_stays_silent() -> None:
    stub = _stub(assistente=False)
    async with stub.client() as http:
        h = Harness(http)
        await h.texto("m1")
        await h.texto("m1")
    assert h.texts == [PAUSADO]


async def test_active_clears_key() -> None:
    stub = _stub(assistente=True)
    stub.json("GET", "/insumos/catalogo", 200, {"itens": [], "total": 0, "proximoCursor": None})
    async with stub.client() as http:
        h = Harness(http)
        h.state.paused_replied.add(BENEFICIARIA)
        h.llm.queue(EXTRACAO_VAZIA)
        await h.texto("m1")
    assert h.state.paused_replied == set()
    assert PAUSADO not in h.texts
    assert h.categorias_lidas == 1


async def test_without_switches_the_flow_is_unchanged() -> None:
    stub = CoreStub()
    stub.json("GET", "/insumos/catalogo", 200, {"itens": [], "total": 0, "proximoCursor": None})
    async with stub.client() as http:
        h = Harness(http, wire_switches=False)
        h.llm.queue(EXTRACAO_VAZIA)
        await h.texto("m1")
    assert h.categorias_lidas == 1
    assert PATH not in [request.url.path for request in stub.requests]


async def test_store_marks_once_and_clears() -> None:
    redis = FakeRedis()
    store = ConversationState(redis, conv_ttl_min=30)
    assert await store.mark_paused_replied(BENEFICIARIA) is True
    assert await store.mark_paused_replied(BENEFICIARIA) is False
    assert await redis.get(f"paused:replied:{BENEFICIARIA}") == "1"
    await store.clear_paused_replied(BENEFICIARIA)
    assert await store.mark_paused_replied(BENEFICIARIA) is True


async def test_key_builder() -> None:
    assert keys.paused_replied(BENEFICIARIA) == f"paused:replied:{BENEFICIARIA}"
