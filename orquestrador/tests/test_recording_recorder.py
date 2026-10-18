"""Item-by-item recording tests on the core stub.

These assert the recorder's guarantees without any network: new supplies are
created before their movement, the idempotency key is exact, per-item
status is isolated in the fake state, one item's failure does not block the
others, and an already-OK item is not re-sent after a restart.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.core.client import CoreClient
from app.core.models import OrigemMovimentacao, UnidadeMedida
from app.core.stub import CoreStub
from app.flow.models import Draft, DraftItem
from app.llm.schemas import AcaoItem
from app.recording import ItemStatus, Recorder
from app.recording.recorder import idempotency_key
from tests.flow_fakes import FakeState

pytestmark = pytest.mark.asyncio


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


def _item(index: int, *, acao=AcaoItem.ATUALIZAR, insumo_id=10, nome="Arroz") -> DraftItem:
    return DraftItem(
        index=index,
        acao=acao,
        insumo_id=insumo_id,
        nome=nome,
        unidade=UnidadeMedida.KG,
        quantidade_total=Decimal(10),
        calculo="10 kg",
    )


def _draft(itens, *, chave=None, fluxo="fluxo-1") -> Draft:
    return Draft(
        fluxo_id=fluxo,
        chave_acesso=chave,
        origem=OrigemMovimentacao.NOTA_IMAGEM if chave else OrigemMovimentacao.TEXTO,
        itens=itens,
    )


async def test_idempotency_key_uses_nota_when_key_present() -> None:
    draft = _draft([_item(0)], chave="A" * 44)
    assert idempotency_key(draft, 10) == f"nota:{'A' * 44}:10"


async def test_idempotency_key_uses_fluxo_when_no_key() -> None:
    draft = _draft([_item(0)], fluxo="fx")
    assert idempotency_key(draft, 10) == "fluxo:fx:10"


async def test_records_item_and_sends_exact_key() -> None:
    stub = CoreStub()
    stub.on("POST", "/estoque/movimentos", _movimento_201)
    state = FakeState()
    draft = _draft([_item(0)], chave="3" * 44)

    async with stub.client() as http:
        result = await Recorder(state, CoreClient(http)).record(draft)

    assert result.all_ok
    sent = stub.requests[0]
    assert sent.headers["Idempotency-Key"] == f"nota:{'3' * 44}:10"
    status = await state.get_item_status("fluxo-1", "item:0")
    assert status["status"] == ItemStatus.OK.value


async def test_new_supply_created_before_movement() -> None:
    stub = CoreStub()
    stub.json(
        "POST",
        "/insumos/obter-ou-criar",
        201,
        {"idInsumo": 77, "nome": "Farinha de mandioca", "unidade": "KG"},
    )
    stub.on("POST", "/estoque/movimentos", _movimento_201)
    state = FakeState()
    novo = _item(0, acao=AcaoItem.CRIAR, insumo_id=None, nome="Farinha de mandioca")
    draft = _draft([novo], fluxo="fx")

    async with stub.client() as http:
        result = await Recorder(state, CoreClient(http)).record(draft)

    assert result.all_ok
    paths = [r.url.path for r in stub.requests]
    assert paths == ["/insumos/obter-ou-criar", "/estoque/movimentos"]
    # The key uses the id returned by get-or-create.
    assert stub.requests[1].headers["Idempotency-Key"] == "fluxo:fx:77"


async def test_transient_failure_marks_item_retryable() -> None:
    stub = CoreStub()
    stub.timeout("POST", "/estoque/movimentos")
    state = FakeState()

    async with stub.client() as http:
        result = await Recorder(state, CoreClient(http)).record(_draft([_item(0)], fluxo="fx"))

    assert result.none_ok
    assert result.has_retryable
    assert result.failed[0].status is ItemStatus.RETRYABLE


async def test_definitive_failure_marks_item_failed() -> None:
    stub = CoreStub()
    stub.status("POST", "/estoque/movimentos", 422)
    state = FakeState()

    async with stub.client() as http:
        result = await Recorder(state, CoreClient(http)).record(_draft([_item(0)], fluxo="fx"))

    assert result.none_ok
    assert not result.has_retryable
    assert result.failed[0].status is ItemStatus.FAILED


async def test_one_failure_does_not_block_others() -> None:
    calls = {"n": 0}

    def movimento(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # The second item times out; the first and third still record.
        if calls["n"] == 2:
            raise httpx.ConnectTimeout("boom", request=request)
        return _movimento_201(request)

    stub = CoreStub()
    stub.on("POST", "/estoque/movimentos", movimento)
    state = FakeState()
    draft = _draft(
        [_item(0, insumo_id=10), _item(1, insumo_id=11), _item(2, insumo_id=12)],
        fluxo="fx",
    )

    async with stub.client() as http:
        result = await Recorder(state, CoreClient(http)).record(draft)

    assert len(result.recorded) == 2
    assert len(result.failed) == 1
    assert result.failed[0].index == 1


async def test_already_ok_item_not_resent_after_restart() -> None:
    stub = CoreStub()
    stub.on("POST", "/estoque/movimentos", _movimento_201)
    state = FakeState()
    draft = _draft([_item(0)], fluxo="fx")
    state.seed_item(
        "fx",
        "item:0",
        {
            "index": 0,
            "nome": "Arroz",
            "status": ItemStatus.OK.value,
            "insumo_id": 10,
            "idempotency_key": "fluxo:fx:10",
        },
    )

    async with stub.client() as http:
        result = await Recorder(state, CoreClient(http)).record(draft)

    assert result.all_ok
    assert stub.requests == []  # the recorded item was not sent again
