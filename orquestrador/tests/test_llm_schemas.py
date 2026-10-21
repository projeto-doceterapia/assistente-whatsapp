from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models import UnidadeMedida
from app.llm.fake import FakeLlmProvider
from app.llm.port import LlmPort, LlmRequest, LlmResponseError
from app.llm.schemas import (
    AcaoItem,
    ExtracaoResultado,
    IntencaoResultado,
    ItemExtraido,
    TipoIntencao,
)


def test_item_extraido_parses_raw_package_fields() -> None:
    item = ItemExtraido(
        acao=AcaoItem.ATUALIZAR,
        insumo_id=7,
        nome_extraido="Açúcar",
        quantidade_embalagens=Decimal(2),
        tamanho_embalagem=Decimal(5),
        unidade_embalagem=UnidadeMedida.KG,
        valor_liquido=Decimal("24.90"),
    )
    assert item.quantidade_embalagens == Decimal(2)
    assert item.unidade_embalagem is UnidadeMedida.KG
    assert item.valor_liquido == Decimal("24.90")
    assert item.termos_busca == []


def test_extracao_resultado_from_recorded_json() -> None:
    raw = (
        '{"itens":[{"acao":"CRIAR","nome_extraido":"Farinha de mandioca",'
        '"quantidade_embalagens":1,"tamanho_embalagem":1,"unidade_embalagem":"KG",'
        '"categoria_id":3,"termos_busca":[]}],"chave_acesso":null}'
    )
    resultado = ExtracaoResultado.model_validate_json(raw)
    assert resultado.itens[0].acao is AcaoItem.CRIAR
    assert resultado.itens[0].categoria_id == 3


async def test_fake_provider_replays_and_validates() -> None:
    raw = '{"intencao":"CONFIRMAR"}'
    provider = FakeLlmProvider([raw])
    assert isinstance(provider, LlmPort)
    out = await provider.generate(LlmRequest(system="sys", text="sim"), IntencaoResultado)
    assert out.intencao is TipoIntencao.CONFIRMAR
    assert provider.requests[0].text == "sim"


async def test_fake_provider_runs_out_of_responses() -> None:
    provider = FakeLlmProvider()
    with pytest.raises(LlmResponseError):
        await provider.generate(LlmRequest(system="s"), IntencaoResultado)
