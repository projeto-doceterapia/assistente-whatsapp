from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.evaluation.manifest import ItemEsperado
from app.evaluation.report import ItemResultado
from app.llm.schemas import AcaoItem
from app.purchase.validacao import ItemValidado


@dataclass
class PontuacaoCaso:
    """Aggregated scoring numbers for one case."""

    itens: list[ItemResultado]
    esperados: int
    computados: int
    acertos: int
    denominador: int
    acertos_categoria: int
    categorias_esperadas: int


def _identidade(item: ItemValidado) -> tuple[object, ...]:
    if item.acao is AcaoItem.ATUALIZAR:
        return ("atualizar", item.insumo_id)
    if item.acao is AcaoItem.CRIAR:
        return ("criar", item.nome.casefold())
    return ("esclarecer",)


def _identidade_esperada(esperado: ItemEsperado) -> tuple[object, ...]:
    if esperado.acao is AcaoItem.ATUALIZAR:
        return ("atualizar", esperado.insumo_id)
    if esperado.acao is AcaoItem.CRIAR:
        nome = (esperado.nome or "").casefold()
        return ("criar", nome)
    return ("esclarecer",)


def _dentro(computado: Decimal | None, esperado: Decimal, tol: Decimal) -> bool:
    if computado is None:
        return False
    return abs(computado - esperado) <= tol


def _conferir(esperado: ItemEsperado, computado: ItemValidado) -> tuple[bool, str]:
    if computado.acao is not esperado.acao:
        return False, f"ação {computado.acao.value} != {esperado.acao.value}"
    if esperado.unidade is not None and computado.unidade is not esperado.unidade:
        return False, f"unidade {computado.unidade.value} != {esperado.unidade.value}"
    if esperado.quantidade_total is not None and not _dentro(
        computado.quantidade_total,
        esperado.quantidade_total,
        esperado.tolerancias.quantidade_abs,
    ):
        return False, f"quantidade {computado.quantidade_total} != {esperado.quantidade_total}"
    if esperado.custo_unitario is not None and not _dentro(
        computado.custo_unitario,
        esperado.custo_unitario,
        esperado.tolerancias.custo_unitario_abs,
    ):
        return False, f"custo {computado.custo_unitario} != {esperado.custo_unitario}"
    if (
        esperado.categoria_nome is not None
        and computado.categoria_nome != esperado.categoria_nome
    ):
        return False, f"categoria {computado.categoria_nome} != {esperado.categoria_nome}"
    return True, "ok"


def _resumo(item: ItemValidado) -> str:
    alvo = item.insumo_id if item.acao is AcaoItem.ATUALIZAR else item.nome
    return f"{item.acao.value}:{alvo} {item.quantidade_total}{item.unidade.value}"


def _resumo_esperado(esperado: ItemEsperado) -> str:
    alvo = esperado.insumo_id if esperado.acao is AcaoItem.ATUALIZAR else esperado.nome
    return f"{esperado.acao.value}:{alvo}"


def pontuar_caso(
    esperados: list[ItemEsperado],
    computados: list[ItemValidado],
) -> PontuacaoCaso:
    """Score one case by greedily pairing expected items with computed ones.

    An expected item is a hit only when a computed item of the same identity
    (action + supply id for ATUALIZAR, action + name for CRIAR, action for
    ESCLARECER) exists and its unit, quantity, cost and category all match within
    tolerance. Unmatched computed items are false positives that enlarge the
    denominator, so over-extraction lowers accuracy.
    """
    restantes = list(computados)
    resultados: list[ItemResultado] = []
    acertos = 0
    acertos_categoria = 0
    categorias_esperadas = 0

    for esperado in esperados:
        if esperado.categoria_nome is not None:
            categorias_esperadas += 1
        alvo = _identidade_esperada(esperado)
        par: ItemValidado | None = None
        for candidato in restantes:
            if _identidade(candidato) == alvo:
                par = candidato
                break
        if par is None:
            resultados.append(
                ItemResultado(
                    esperado=_resumo_esperado(esperado),
                    computado=None,
                    acertou=False,
                    motivo="sem item correspondente",
                )
            )
            continue
        restantes.remove(par)
        ok, motivo = _conferir(esperado, par)
        if ok:
            acertos += 1
            if (
                esperado.categoria_nome is not None
                and par.categoria_nome == esperado.categoria_nome
            ):
                acertos_categoria += 1
        resultados.append(
            ItemResultado(
                esperado=_resumo_esperado(esperado),
                computado=_resumo(par),
                acertou=ok,
                motivo=motivo,
            )
        )

    for extra in restantes:
        resultados.append(
            ItemResultado(
                esperado="(nenhum)",
                computado=_resumo(extra),
                acertou=False,
                motivo="item extra não esperado",
            )
        )

    denominador = max(len(esperados), len(computados))
    return PontuacaoCaso(
        itens=resultados,
        esperados=len(esperados),
        computados=len(computados),
        acertos=acertos,
        denominador=denominador,
        acertos_categoria=acertos_categoria,
        categorias_esperadas=categorias_esperadas,
    )
