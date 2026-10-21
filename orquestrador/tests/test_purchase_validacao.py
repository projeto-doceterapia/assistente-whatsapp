from __future__ import annotations

from decimal import Decimal

from app.core.models import CatalogoItem, UnidadeMedida
from app.llm.schemas import AcaoItem, ItemExtraido
from app.purchase.calculo import (
    UnidadeIncompativelError,
    converter,
    custo_unitario,
    quantidade_total,
)
from app.purchase.rendering import Categoria
from app.purchase.validacao import consolidar, validar_item

CATALOGO = {
    7: CatalogoItem(idInsumo=7, nome="Açúcar", unidade=UnidadeMedida.G),
    9: CatalogoItem(idInsumo=9, nome="Leite", unidade=UnidadeMedida.ML),
}
CATEGORIAS = {3: Categoria(idCategoriaInsumo=3, nome="Farinhas")}


def _item(**kwargs: object) -> ItemExtraido:
    base = dict(
        acao=AcaoItem.ATUALIZAR,
        insumo_id=7,
        nome_extraido="Açúcar",
        quantidade_embalagens=Decimal(2),
        tamanho_embalagem=Decimal(5),
        unidade_embalagem=UnidadeMedida.KG,
    )
    base.update(kwargs)
    return ItemExtraido(**base)


def test_conversion_2x5kg_to_grams() -> None:
    total = quantidade_total(Decimal(2), Decimal(5), UnidadeMedida.KG, UnidadeMedida.G)
    assert total == Decimal(10000)


def test_converter_rejects_incompatible_units() -> None:
    try:
        converter(Decimal(1), UnidadeMedida.KG, UnidadeMedida.ML)
    except UnidadeIncompativelError as exc:
        assert exc.de is UnidadeMedida.KG
    else:  # pragma: no cover
        raise AssertionError("expected UnidadeIncompativelError")


def test_custo_unitario_net_value_over_quantity() -> None:
    # 24.90 / 10000 g rounded half-even at scale 4
    assert custo_unitario(Decimal("24.90"), Decimal(10000)) == Decimal("0.0025")


def test_validar_item_computes_and_formats() -> None:
    out = validar_item(_item(valor_liquido=Decimal("24.90")), CATALOGO, CATEGORIAS)
    assert out.acao is AcaoItem.ATUALIZAR
    assert out.quantidade_total == Decimal(10000)
    assert out.calculo == "2 x 5 kg = 10.000 g"
    assert out.custo_unitario == Decimal("0.0025")
    assert out.nome == "Açúcar"


def test_invented_id_becomes_clarify() -> None:
    out = validar_item(_item(insumo_id=999), CATALOGO, CATEGORIAS)
    assert out.acao is AcaoItem.ESCLARECER


def test_incompatible_unit_becomes_clarify() -> None:
    # supply 9 is ML, package in KG cannot convert
    out = validar_item(
        _item(acao=AcaoItem.ATUALIZAR, insumo_id=9, unidade_embalagem=UnidadeMedida.KG),
        CATALOGO,
        CATEGORIAS,
    )
    assert out.acao is AcaoItem.ESCLARECER


def test_nonpositive_and_above_limit_become_clarify() -> None:
    zero = validar_item(_item(quantidade_embalagens=Decimal(0)), CATALOGO, CATEGORIAS)
    enorme = validar_item(_item(tamanho_embalagem=Decimal(200000)), CATALOGO, CATEGORIAS)
    assert zero.acao is AcaoItem.ESCLARECER
    assert enorme.acao is AcaoItem.ESCLARECER


def test_unknown_category_falls_back_to_default() -> None:
    novo = _item(
        acao=AcaoItem.CRIAR,
        insumo_id=None,
        nome_extraido="Farinha",
        unidade=UnidadeMedida.KG,
        categoria_id=999,
    )
    out = validar_item(novo, CATALOGO, CATEGORIAS)
    assert out.acao is AcaoItem.CRIAR
    assert out.categoria_id is None
    assert out.categoria_nome == "A classificar"


def test_known_category_kept() -> None:
    novo = _item(
        acao=AcaoItem.CRIAR,
        insumo_id=None,
        nome_extraido="Farinha",
        unidade=UnidadeMedida.KG,
        categoria_id=3,
    )
    out = validar_item(novo, CATALOGO, CATEGORIAS)
    assert out.categoria_id == 3
    assert out.categoria_nome == "Farinhas"


def test_consolidation_sums_quantities_and_net_values() -> None:
    linha = _item(
        quantidade_embalagens=Decimal(1),
        tamanho_embalagem=Decimal(1),
        unidade_embalagem=UnidadeMedida.KG,
        valor_liquido=Decimal("5.00"),
    )
    validados = [
        validar_item(linha, CATALOGO, CATEGORIAS),
        validar_item(linha, CATALOGO, CATEGORIAS),
    ]
    consolidado = consolidar(validados)
    assert len(consolidado) == 1
    assert consolidado[0].quantidade_total == Decimal(2000)
    assert consolidado[0].valor_liquido == Decimal("10.00")
    assert consolidado[0].calculo == "2.000 g"
    # 10.00 / 2000 g
    assert consolidado[0].custo_unitario == Decimal("0.0050")


def test_consolidation_keeps_clarify_lines_apart() -> None:
    ok = validar_item(_item(), CATALOGO, CATEGORIAS)
    clarify = validar_item(_item(insumo_id=999), CATALOGO, CATEGORIAS)
    consolidado = consolidar([ok, clarify])
    assert len(consolidado) == 2
