"""Handler tests for beneficiary phone matching.

They assert that a message is accepted when the sender identifies the
configured beneficiary (plus sign vs digits, missing ninth digit) and ignored
otherwise, and that the ignore path emits a diagnostic record carrying only
lengths and the last four digits, never a full number.
"""

from __future__ import annotations

import logging
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
from tests.flow_fakes import FakeState

pytestmark = pytest.mark.asyncio

# Configured beneficiary in E.164 with a plus sign, as documented.
BENEFICIARIA = "+5511999999999"
BENEFICIARIA_DIGITS = "5511999999999"
BENEFICIARIA_NO_NINTH = "551199999999"
GROUP_ID = "120363021234567890"
CATEGORIAS = [Categoria(idCategoriaInsumo=1, nome="Farinhas")]


class FakeClassifier:
    async def classify(self, text: str) -> ReplyIntent:
        return ReplyIntent.OTHER


def _item_json() -> str:
    return (
        '{"acao":"ATUALIZAR","insumo_id":1,"nome_extraido":"Arroz",'
        '"quantidade_embalagens":2,"tamanho_embalagem":5,'
        '"unidade_embalagem":"KG","unidade":"KG","termos_busca":[]}'
    )


def _extracao_json() -> str:
    return f'{{"itens":[{_item_json()}],"chave_acesso":null}}'


_CATALOGO = {
    "itens": [{"idInsumo": 1, "nome": "Arroz", "unidade": "KG"}],
    "total": 1,
    "proximoCursor": None,
}


def _stub() -> CoreStub:
    stub = CoreStub()
    stub.json("GET", "/insumos/catalogo", 200, _CATALOGO)
    return stub


class Harness:
    def __init__(self, http: httpx.AsyncClient, responses: list[str]) -> None:
        self.channel = FakeChannel()
        self.state = FakeState()
        llm = FakeLlmProvider(responses)
        deps = FlowDeps(
            state=self.state,
            channel=self.channel,
            core=CoreClient(http),
            llm=llm,
            classifier=FakeClassifier(),
            extracao=ExtracaoService(llm, CoreClient(http)),
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

    async def inbound(self, sender: str, mid: str) -> None:
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=sender,
                kind=MessageKind.TEXT,
                timestamp=datetime.now(tz=timezone.utc),
                text="comprei 2 x 5 kg de arroz",
            )
        )


async def test_sender_with_plus_is_processed() -> None:
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, [_extracao_json()])
        await h.inbound(BENEFICIARIA_DIGITS, "m1")
    assert any("Recebi" in t for t in h.texts)


async def test_sender_missing_ninth_digit_is_processed() -> None:
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, [_extracao_json()])
        await h.inbound(BENEFICIARIA_NO_NINTH, "m1")
    assert any("Recebi" in t for t in h.texts)


async def test_group_sender_is_ignored_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = _stub()
    with caplog.at_level(logging.INFO, logger="flow.handler"):
        async with stub.client() as http:
            h = Harness(http, [_extracao_json()])
            await h.inbound(GROUP_ID, "m1")

    assert h.texts == []
    records = [
        r for r in caplog.records if r.getMessage() == "inbound_ignored_sender_mismatch"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.sender_len == len(GROUP_ID)
    assert record.sender_tail == GROUP_ID[-4:]
    assert record.expected_len == len(BENEFICIARIA_DIGITS)
    assert record.expected_tail == BENEFICIARIA_DIGITS[-4:]
    # No full number appears anywhere in the captured record.
    blob = record.getMessage() + repr(record.__dict__)
    assert GROUP_ID not in blob
    assert BENEFICIARIA_DIGITS not in blob
