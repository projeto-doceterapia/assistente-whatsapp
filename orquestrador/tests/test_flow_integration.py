"""End-to-end flow tests on the fake channel, core stub and fake LLM.

No network and no Redis: the handler runs against `FakeChannel`, `CoreStub`,
`FakeLlmProvider`, `FakeState` and a scripted `FakeClassifier`. Each test maps to
a happy-path confirmation scenario or to the partial /
duplicate / transient-failure scenarios.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import httpx
import pytest
from PIL import Image

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
from tests.flow_fakes import FakeState
from tests.invoice_fixtures import VALID_KEY

pytestmark = pytest.mark.asyncio

BENEFICIARIA = "5511999999999"
CATEGORIAS = [Categoria(idCategoriaInsumo=1, nome="Farinhas")]


class FakeClassifier:
    """ClassifierPort that replays scripted reply intents."""

    def __init__(self) -> None:
        self._intents: list[ReplyIntent] = []
        self.calls: list[str] = []

    def queue(self, intent: ReplyIntent) -> None:
        self._intents.append(intent)

    async def classify(self, text: str) -> ReplyIntent:
        self.calls.append(text)
        if not self._intents:
            return ReplyIntent.OTHER
        return self._intents.pop(0)


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _catalogo_json(itens: list[dict]) -> dict:
    return {"itens": itens, "total": len(itens), "proximoCursor": None}


_CATALOGO = _catalogo_json(
    [
        {"idInsumo": 1, "nome": "Arroz", "unidade": "KG"},
        {"idInsumo": 2, "nome": "Feijão", "unidade": "KG"},
    ]
)


def _movimento_201(request: httpx.Request) -> httpx.Response:
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


def _item_json(
    *, acao="ATUALIZAR", insumo_id=1, nome="Arroz", qtd="2", tam="5", un="KG"
) -> str:
    idf = "null" if insumo_id is None else str(insumo_id)
    return (
        f'{{"acao":"{acao}","insumo_id":{idf},"nome_extraido":"{nome}",'
        f'"quantidade_embalagens":{qtd},"tamanho_embalagem":{tam},'
        f'"unidade_embalagem":"{un}","unidade":"{un}","termos_busca":[]}}'
    )


def _extracao_json(itens: list[str], chave: str | None = None) -> str:
    chave_json = "null" if chave is None else f'"{chave}"'
    return f'{{"itens":[{",".join(itens)}],"chave_acesso":{chave_json}}}'


def _ajuste_json(itens: list[str]) -> str:
    return f'{{"itens":[{",".join(itens)}]}}'


class Harness:
    """Wires one handler with in-memory doubles and exposes the sent texts."""

    def __init__(self, http: httpx.AsyncClient, llm: FakeLlmProvider) -> None:
        self.channel = FakeChannel()
        self.state = FakeState()
        self.classifier = FakeClassifier()
        self.llm = llm
        core = CoreClient(http)
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
        self.handler = InboundHandler(deps)

    async def _categorias(self) -> list[Categoria]:
        return CATEGORIAS

    @property
    def texts(self) -> list[str]:
        return [t.text for t in self.channel.sent_texts]

    @property
    def last(self) -> str:
        return self.channel.sent_texts[-1].text

    async def send_text(self, text: str, mid: str) -> None:
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.TEXT,
                timestamp=datetime.now(tz=timezone.utc),
                text=text,
            )
        )

    async def send_image(self, mid: str, media_ref: str = "ref-1") -> None:
        self.channel.stage_media(media_ref, _png_bytes())
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.IMAGE,
                timestamp=datetime.now(tz=timezone.utc),
                media_ref=media_ref,
            )
        )

    async def send_audio(self, mid: str) -> None:
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.UNSUPPORTED,
                timestamp=datetime.now(tz=timezone.utc),
            )
        )


def _stub(*, movimento=True, nota=None) -> CoreStub:
    stub = CoreStub()
    stub.json("GET", "/insumos/catalogo", 200, _CATALOGO)
    if movimento:
        stub.on("POST", "/estoque/movimentos", _movimento_201)
    if nota is not None:
        stub.json("GET", f"/notas-fiscais/{VALID_KEY}/status", 200, nota)
    return stub


# --- all items in the catalog, confirm, success ---------------------


async def test_text_confirm_records_all() -> None:
    llm = FakeLlmProvider([_extracao_json([_item_json(nome="Arroz")])])
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("comprei 2 x 5 kg de arroz", "m1")
        assert any("Recebi" in t for t in h.texts)
        assert "Entendi esta compra" in h.last
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.send_text("sim, pode registrar", "m2")

    assert "Pronto! Atualizei seu estoque" in h.last
    assert "Arroz" in h.last


# --- new item approved -> created and recorded ----------------------


async def test_new_item_created_and_recorded() -> None:
    novo = _item_json(acao="CRIAR", insumo_id=None, nome="Farinha de mandioca", qtd="1", tam="1")
    llm = FakeLlmProvider([_extracao_json([novo])])
    stub = _stub()
    stub.json(
        "POST",
        "/insumos/obter-ou-criar",
        201,
        {"idInsumo": 50, "nome": "Farinha de mandioca", "unidade": "KG"},
    )
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("comprei 1 kg de farinha de mandioca", "m1")
        assert "não está no seu cadastro" in h.last
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.send_text("sim", "m2")

    assert "Pronto! Atualizei seu estoque" in h.last
    paths = [r.url.path for r in stub.requests]
    assert "/insumos/obter-ou-criar" in paths
    criar_idx = paths.index("/insumos/obter-ou-criar")
    mov_idx = paths.index("/estoque/movimentos")
    assert criar_idx < mov_idx


# --- free-text quantity adjustment, re-presented --------------------


async def test_adjustment_re_presents_draft() -> None:
    first = _extracao_json([_item_json(nome="Arroz", qtd="2", tam="1")])
    ajuste = _ajuste_json([
        '{"nome_extraido":"Arroz","quantidade_embalagens":3,"tamanho_embalagem":1,"unidade_embalagem":"KG"}'
    ])
    llm = FakeLlmProvider([first, ajuste])
    stub = _stub(movimento=False)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("comprei arroz", "m1")
        h.classifier.queue(ReplyIntent.ADJUST)
        await h.send_text("o arroz foi 3 kg", "m2")

    assert "Entendi esta compra" in h.last
    assert "3 kg" in h.last


# --- third adjustment ends the flow ---------------------------------


async def test_third_adjustment_ends_flow() -> None:
    base = _extracao_json([_item_json(nome="Arroz", qtd="2", tam="1")])
    ajuste = _ajuste_json([
        '{"nome_extraido":"Arroz","quantidade_embalagens":3,"tamanho_embalagem":1,"unidade_embalagem":"KG"}'
    ])
    llm = FakeLlmProvider([base, ajuste, ajuste])
    stub = _stub(movimento=False)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("comprei arroz", "m1")
        h.classifier.queue(ReplyIntent.ADJUST)
        await h.send_text("foi 3 kg", "m2")
        h.classifier.queue(ReplyIntent.ADJUST)
        await h.send_text("foi 4 kg", "m3")
        h.classifier.queue(ReplyIntent.ADJUST)
        await h.send_text("foi 5 kg", "m4")

    assert "foto mais nítida" in h.last
    assert await h.state.load_conversation(BENEFICIARIA) == {}


# --- duplicate: whole invoice already registered -------------------


async def test_duplicate_invoice_records_nothing() -> None:
    llm = FakeLlmProvider(
        [_extracao_json([_item_json(nome="Arroz", insumo_id=1)], chave=VALID_KEY)]
    )
    stub = _stub(movimento=False, nota={"status": "REGISTRADA", "insumoIds": [1]})
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_image("m1")

    assert "já foi registrada" in h.last
    assert all(r.url.path != "/estoque/movimentos" for r in stub.requests)


# --- partial receipt: only the pending item is confirmed -------------------


async def test_partial_invoice_confirms_only_pending() -> None:
    itens = [
        _item_json(nome="Arroz", insumo_id=1),
        _item_json(nome="Feijão", insumo_id=2),
    ]
    llm = FakeLlmProvider([_extracao_json(itens, chave=VALID_KEY)])
    stub = _stub(nota={"status": "REGISTRADA", "insumoIds": [1]})
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_image("m1")
        assert "Parte dessa nota já estava registrada" in h.last
        assert "Feijão" in h.last
        assert "Arroz" not in h.last.split("Pode confirmar")[0]
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.send_text("sim", "m2")

    assert "Pronto! Atualizei seu estoque" in h.last


# --- generic item -> clarify, nothing recorded ----------------------


async def test_generic_item_clarify_records_nothing() -> None:
    clarify = (
        '{"acao":"ESCLARECER","nome_extraido":"alguma coisa",'
        '"quantidade_embalagens":2,"tamanho_embalagem":1,"unidade_embalagem":"KG",'
        '"termos_busca":[]}'
    )
    llm = FakeLlmProvider([_extracao_json([clarify])])
    stub = _stub(movimento=False)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("comprei 2 kg de alguma coisa", "m1")

    assert "Entendi esta compra" in h.last
    assert "❓" in h.last
    assert all(r.url.path != "/estoque/movimentos" for r in stub.requests)


# --- unsupported type -----------------------------------------------


async def test_audio_lists_accepted_formats() -> None:
    llm = FakeLlmProvider([])
    stub = _stub(movimento=False)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_audio("m1")

    assert "Só consigo ler foto da nota fiscal ou texto da compra" in h.last


# --- transient failure on one item, others still recorded ------------------


async def test_transient_failure_one_item_others_recorded() -> None:
    itens = [_item_json(nome="Arroz", insumo_id=1), _item_json(nome="Feijão", insumo_id=2)]
    llm = FakeLlmProvider([_extracao_json(itens)])
    calls = {"n": 0}

    def movimento(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.ConnectTimeout("down", request=request)
        return _movimento_201(request)

    stub = _stub(movimento=False)
    stub.on("POST", "/estoque/movimentos", movimento)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("comprei arroz e feijão", "m1")
        h.classifier.queue(ReplyIntent.CONFIRM)
        await h.send_text("sim", "m2")

    assert "Registrei estes itens" in h.last
    assert "Não consegui registrar" in h.last
    assert "tenta de novo" in h.last


# --- dedup and loose reply --------------------------------------------------


async def test_duplicate_inbound_is_ignored() -> None:
    llm = FakeLlmProvider([_extracao_json([_item_json(nome="Arroz")])])
    stub = _stub(movimento=False)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("comprei arroz", "same")
        count_after_first = len(h.channel.sent_texts)
        await h.send_text("comprei arroz", "same")

    assert len(h.channel.sent_texts) == count_after_first


async def test_loose_reply_with_no_flow_says_nothing_pending() -> None:
    llm = FakeLlmProvider([_extracao_json([])])
    stub = _stub(movimento=False)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("ok", "m1")

    assert "Não tem nada pendente" in h.last


async def test_message_from_other_sender_is_dropped() -> None:
    llm = FakeLlmProvider([])
    stub = _stub(movimento=False)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.handler.handle(
            InboundMessage(
                id="x1",
                sender="5511000000000",
                kind=MessageKind.TEXT,
                timestamp=datetime.now(tz=timezone.utc),
                text="oi",
            )
        )

    assert h.channel.sent_texts == []


async def test_expiry_starts_fresh() -> None:
    llm = FakeLlmProvider(
        [
            _extracao_json([_item_json(nome="Arroz")]),
            _extracao_json([_item_json(nome="Arroz")]),
        ]
    )
    stub = _stub(movimento=False)
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("comprei arroz", "m1")
        assert "Entendi esta compra" in h.last
        h.state.expire(BENEFICIARIA)
        await h.send_text("comprei arroz de novo", "m2")

    # The second message, after expiry, starts a brand new reading.
    assert h.texts.count("Recebi! Estou lendo, um momento…") == 2
