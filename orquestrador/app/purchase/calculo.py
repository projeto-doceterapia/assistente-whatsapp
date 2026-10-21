from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from app.core.models import UnidadeMedida


class UnidadeIncompativelError(ValueError):
    """Raised when a package unit cannot be converted to the supply unit."""

    def __init__(self, de: UnidadeMedida, para: UnidadeMedida) -> None:
        super().__init__(f"unidade {de.value} incompatível com {para.value}")
        self.de = de
        self.para = para


# Conversion factors to the base unit of each compatible group. The
# orchestrator mirrors the core's table; contract tests keep them consistent.
_PARA_BASE: dict[UnidadeMedida, tuple[UnidadeMedida, Decimal]] = {
    UnidadeMedida.KG: (UnidadeMedida.G, Decimal(1000)),
    UnidadeMedida.G: (UnidadeMedida.G, Decimal(1)),
    UnidadeMedida.L: (UnidadeMedida.ML, Decimal(1000)),
    UnidadeMedida.ML: (UnidadeMedida.ML, Decimal(1)),
    UnidadeMedida.UNIDADE: (UnidadeMedida.UNIDADE, Decimal(1)),
}


def _grupo(unidade: UnidadeMedida) -> UnidadeMedida:
    return _PARA_BASE[unidade][0]


def compativel(de: UnidadeMedida, para: UnidadeMedida) -> bool:
    """Whether a value in unit ``de`` can be expressed in unit ``para``."""
    return _grupo(de) == _grupo(para)


def converter(valor: Decimal, de: UnidadeMedida, para: UnidadeMedida) -> Decimal:
    """Convert ``valor`` from unit ``de`` to unit ``para``.

    Raises :class:`UnidadeIncompativelError` for incompatible units.
    """
    if not compativel(de, para):
        raise UnidadeIncompativelError(de, para)
    base = valor * _PARA_BASE[de][1]
    return base / _PARA_BASE[para][1]


def quantidade_total(
    quantidade_embalagens: Decimal,
    tamanho_embalagem: Decimal,
    unidade_embalagem: UnidadeMedida,
    unidade_insumo: UnidadeMedida,
) -> Decimal:
    """Total quantity in the supply unit from raw package data.

    Example: 2 packages of 5 KG for a supply in G -> 10 000 g.
    """
    total_na_embalagem = quantidade_embalagens * tamanho_embalagem
    return converter(total_na_embalagem, unidade_embalagem, unidade_insumo)


def custo_unitario(valor_liquido: Decimal, quantidade_convertida: Decimal) -> Decimal:
    """Net unit cost = net line value / quantity in the supply unit.

    Scale 4, HALF_EVEN, matching the core. The caller guarantees a positive
    quantity.
    """
    bruto = valor_liquido / quantidade_convertida
    return bruto.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def formatar_calculo(
    quantidade_embalagens: Decimal,
    tamanho_embalagem: Decimal,
    unidade_embalagem: UnidadeMedida,
    total: Decimal,
    unidade_insumo: UnidadeMedida,
) -> str:
    """Render the full calculation for the confirmation, e.g. "2 x 5 kg = 10.000 g".

    Uses PT-BR number formatting (thousands with ".") for user-facing text.
    """
    qtd = _formatar_numero(quantidade_embalagens)
    tam = _formatar_numero(tamanho_embalagem)
    tot = _formatar_numero(total)
    return (
        f"{qtd} x {tam} {unidade_embalagem.value.lower()} = "
        f"{tot} {unidade_insumo.value.lower()}"
    )


def _formatar_numero(valor: Decimal) -> str:
    normalizado = valor.normalize()
    inteiro = normalizado == normalizado.to_integral_value()
    if inteiro:
        texto = f"{int(normalizado):,}".replace(",", ".")
        return texto
    # Non-integers keep a comma decimal separator (PT-BR), thousands with a dot.
    partes = f"{normalizado:,.3f}".split(".")
    milhar = partes[0].replace(",", ".")
    return f"{milhar},{partes[1]}"
