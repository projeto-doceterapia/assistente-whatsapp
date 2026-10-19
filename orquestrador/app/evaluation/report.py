from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal


@dataclass
class ItemResultado:
    """Scoring outcome for one expected line of a case."""

    esperado: str
    computado: str | None
    acertou: bool
    motivo: str


@dataclass
class CasoResultado:
    """Scoring outcome for one case."""

    id: str
    tipo: str
    itens_esperados: int
    itens_computados: int
    acertos: int
    denominador: int
    latencia_ms: float
    itens: list[ItemResultado] = field(default_factory=list)
    erro: str | None = None


@dataclass
class Uso:
    """Token usage and cost estimate, when the provider reports it."""

    disponivel: bool
    motivo: str | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    custo_estimado_usd: str | None = None


@dataclass
class Relatorio:
    """Full evaluation report for one provider run over one set."""

    provider: str
    set_versao: str
    threshold: str
    gerado_em: str
    total_casos: int
    total_itens_esperados: int
    total_acertos: int
    total_denominador: int
    acuracia_por_item: float
    acuracia_categoria: float | None
    latencia_p50_ms: float
    latencia_p95_ms: float
    uso: Uso
    aprovado: bool
    observacoes: list[str]
    casos: list[CasoResultado]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def _fmt_pct(valor: float | None) -> str:
    if valor is None:
        return "n/d"
    return f"{valor * 100:.1f}%"


def render_tabela(relatorio: Relatorio) -> str:
    """Render a compact, human-readable summary table of the report."""
    linhas: list[str] = []
    linhas.append(f"Avaliação de IA — provider={relatorio.provider} set=v{relatorio.set_versao}")
    linhas.append("-" * 64)
    cabecalho = f"{'caso':24} {'tipo':7} {'acertos':>9} {'lat(ms)':>9}"
    linhas.append(cabecalho)
    for caso in relatorio.casos:
        acertos = f"{caso.acertos}/{caso.denominador}"
        erro = f"  ERRO: {caso.erro}" if caso.erro else ""
        linhas.append(
            f"{caso.id:24} {caso.tipo:7} {acertos:>9} {caso.latencia_ms:>9.1f}{erro}"
        )
    linhas.append("-" * 64)
    linhas.append(
        f"acurácia por item: {_fmt_pct(relatorio.acuracia_por_item)} "
        f"({relatorio.total_acertos}/{relatorio.total_denominador})  "
        f"limite: {_fmt_pct(float(Decimal(relatorio.threshold)))}"
    )
    linhas.append(f"acurácia de categoria: {_fmt_pct(relatorio.acuracia_categoria)}")
    linhas.append(
        f"latência p50: {relatorio.latencia_p50_ms:.1f} ms  "
        f"p95: {relatorio.latencia_p95_ms:.1f} ms"
    )
    if relatorio.uso.disponivel:
        linhas.append(
            f"tokens: {relatorio.uso.total_tokens}  "
            f"custo estimado: {relatorio.uso.custo_estimado_usd or 'n/d'} USD"
        )
    else:
        linhas.append(f"uso/custo: indisponível ({relatorio.uso.motivo})")
    for obs in relatorio.observacoes:
        linhas.append(f"nota: {obs}")
    linhas.append(f"resultado: {'APROVADO' if relatorio.aprovado else 'REPROVADO'}")
    return "\n".join(linhas)
