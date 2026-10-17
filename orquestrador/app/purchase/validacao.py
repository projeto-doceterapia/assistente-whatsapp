from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from app.core.models import CatalogoItem, UnidadeMedida
from app.llm.schemas import AcaoItem, ItemExtraido
from app.purchase.calculo import (
    UnidadeIncompativelError,
    custo_unitario,
    formatar_calculo,
    quantidade_total,
)
from app.purchase.rendering import CATEGORIA_PADRAO, Categoria

# Upper bound for a single extracted package count or size (design: reject
# above-limit quantities). A receipt line beyond this is treated as an extraction
# error rather than a real purchase.
LIMITE_QUANTIDADE = Decimal(100000)


class ItemValidado(BaseModel):
    """A draft line after code-side validation and computation.

    Quantities and the cost are computed in code; the calculation
    string is ready for the confirmation message. ``acao`` may be downgraded from
    the AI's proposal (e.g. an invented id becomes ESCLARECER).
    """

    acao: AcaoItem
    insumo_id: int | None = None
    nome: str
    unidade: UnidadeMedida
    quantidade_total: Decimal
    calculo: str
    valor_liquido: Decimal | None = None
    custo_unitario: Decimal | None = None
    categoria_id: int | None = None
    categoria_nome: str | None = None


def _unidade_insumo(
    item: ItemExtraido, catalogo: dict[int, CatalogoItem]
) -> UnidadeMedida:
    if item.insumo_id is not None and item.insumo_id in catalogo:
        return catalogo[item.insumo_id].unidade
    return item.unidade or item.unidade_embalagem


def _resolver_categoria(
    item: ItemExtraido, categorias: dict[int, Categoria]
) -> tuple[int | None, str]:
    # Unknown or missing category falls back to the default.
    if item.categoria_id is not None and item.categoria_id in categorias:
        cat = categorias[item.categoria_id]
        return cat.idCategoriaInsumo, cat.nome
    return None, CATEGORIA_PADRAO


def _esclarecer(nome: str, unidade: UnidadeMedida) -> ItemValidado:
    return ItemValidado(
        acao=AcaoItem.ESCLARECER,
        nome=nome,
        unidade=unidade,
        quantidade_total=Decimal(0),
        calculo="",
    )


def validar_item(
    item: ItemExtraido,
    catalogo: dict[int, CatalogoItem],
    categorias: dict[int, Categoria],
) -> ItemValidado:
    """Validate one AI line and compute its quantities.

    - An ATUALIZAR with an id not in the catalog is downgraded to ESCLARECER.
    - A non-positive or above-limit quantity becomes ESCLARECER.
    - An incompatible package unit becomes ESCLARECER.
    - A CRIAR with an unknown category falls back to the default category.
    """
    nome = item.nome_extraido.strip()
    unidade = _unidade_insumo(item, catalogo)

    if item.acao is AcaoItem.ESCLARECER:
        return _esclarecer(nome, unidade)

    if item.acao is AcaoItem.ATUALIZAR and (
        item.insumo_id is None or item.insumo_id not in catalogo
    ):
        return _esclarecer(nome, unidade)

    if (
        item.quantidade_embalagens <= 0
        or item.tamanho_embalagem <= 0
        or item.quantidade_embalagens > LIMITE_QUANTIDADE
        or item.tamanho_embalagem > LIMITE_QUANTIDADE
    ):
        return _esclarecer(nome, unidade)

    try:
        total = quantidade_total(
            item.quantidade_embalagens,
            item.tamanho_embalagem,
            item.unidade_embalagem,
            unidade,
        )
    except UnidadeIncompativelError:
        return _esclarecer(nome, unidade)

    calculo = formatar_calculo(
        item.quantidade_embalagens,
        item.tamanho_embalagem,
        item.unidade_embalagem,
        total,
        unidade,
    )
    custo = custo_unitario(item.valor_liquido, total) if item.valor_liquido else None

    categoria_id: int | None = None
    categoria_nome: str | None = None
    if item.acao is AcaoItem.CRIAR:
        categoria_id, categoria_nome = _resolver_categoria(item, categorias)
    insumo_id = item.insumo_id if item.acao is AcaoItem.ATUALIZAR else None

    return ItemValidado(
        acao=item.acao,
        insumo_id=insumo_id,
        nome=catalogo[insumo_id].nome if insumo_id is not None else nome,
        unidade=unidade,
        quantidade_total=total,
        calculo=calculo,
        valor_liquido=item.valor_liquido,
        custo_unitario=custo,
        categoria_id=categoria_id,
        categoria_nome=categoria_nome,
    )


def _chave_consolidacao(item: ItemValidado) -> tuple[object, ...]:
    if item.insumo_id is not None:
        return ("insumo", item.insumo_id)
    return ("novo", item.nome.casefold(), item.unidade)


def consolidar(itens: list[ItemValidado]) -> list[ItemValidado]:
    """Merge lines of the same supply into one.

    Sums quantities and net values, recomputes the net unit cost, and rebuilds
    the calculation string as the sum. ESCLARECER lines are never merged.
    """
    agrupados: dict[tuple[object, ...], ItemValidado] = {}
    ordem: list[tuple[object, ...]] = []
    resultado: list[ItemValidado] = []

    for item in itens:
        if item.acao is AcaoItem.ESCLARECER:
            resultado.append(item)
            continue
        chave = _chave_consolidacao(item)
        if chave not in agrupados:
            agrupados[chave] = item.model_copy(deep=True)
            ordem.append(chave)
            continue
        atual = agrupados[chave]
        atual.quantidade_total += item.quantidade_total
        if item.valor_liquido is not None:
            atual.valor_liquido = (atual.valor_liquido or Decimal(0)) + item.valor_liquido

    for chave in ordem:
        item = agrupados[chave]
        if item.valor_liquido is not None and item.quantidade_total > 0:
            item.custo_unitario = custo_unitario(item.valor_liquido, item.quantidade_total)
        item.calculo = _recompor_calculo(item)
        resultado.append(item)

    return resultado


def _recompor_calculo(item: ItemValidado) -> str:
    from app.purchase.calculo import _formatar_numero  # noqa: PLC0415 - local helper reuse

    total = _formatar_numero(item.quantidade_total)
    return f"{total} {item.unidade.value.lower()}"
