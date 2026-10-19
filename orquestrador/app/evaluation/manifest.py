from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from app.core.models import CatalogoItem, UnidadeMedida
from app.llm.schemas import AcaoItem
from app.purchase.rendering import Categoria


class TipoEntrada(str, Enum):
    """Input kind for a case: free text or a receipt photo."""

    TEXTO = "texto"
    IMAGEM = "imagem"


class Tolerancias(BaseModel):
    """How close a computed value must be to its expected value to count as a hit.

    Quantities and the net unit cost are computed in code from
    raw package facts the AI returns, so small rounding differences are expected.
    ``quantidade_abs`` is an absolute tolerance in the supply unit;
    ``custo_unitario_abs`` is absolute in currency per supply unit.
    """

    quantidade_abs: Decimal = Field(default=Decimal("0.001"), ge=0)
    custo_unitario_abs: Decimal = Field(default=Decimal("0.0001"), ge=0)


class ItemEsperado(BaseModel):
    """One expected validated line, written by hand for a case.

    Mirrors the fields of ``app.purchase.validacao.ItemValidado`` that matter for
    scoring. ``acao`` says whether the line should match a catalog supply
    (``ATUALIZAR`` with ``insumo_id``), propose a new one (``CRIAR`` with
    ``categoria_nome``) or ask for clarification (``ESCLARECER``). Quantities and
    cost are the values the pipeline must compute; ``None`` means "not scored".
    """

    acao: AcaoItem
    insumo_id: int | None = Field(
        default=None,
        description="Expected catalog id when acao=ATUALIZAR.",
    )
    nome: str | None = Field(
        default=None,
        description="Expected supply name (new item or clarification), compared casefolded.",
    )
    unidade: UnidadeMedida | None = Field(
        default=None,
        description="Expected supply unit of the line.",
    )
    quantidade_total: Decimal | None = Field(
        default=None,
        description="Expected total quantity in the supply unit, within tolerance.",
    )
    custo_unitario: Decimal | None = Field(
        default=None,
        description="Expected net unit cost, within tolerance; None when no price.",
    )
    categoria_nome: str | None = Field(
        default=None,
        description="Expected category name for acao=CRIAR (e.g. 'A classificar').",
    )
    tolerancias: Tolerancias = Field(default_factory=Tolerancias)


class CasoAvaliacao(BaseModel):
    """One evaluation case: an input and its hand-written expected output."""

    id: str = Field(description="Stable case id, used in the report and in recordings.")
    descricao: str = Field(description="Short PT-BR note on what the case exercises.")
    tipo: TipoEntrada
    texto: str | None = Field(
        default=None,
        description="Purchase text (tipo=texto). Required for text cases.",
    )
    imagem: str | None = Field(
        default=None,
        description="Relative path under images/ for a receipt photo (tipo=imagem).",
    )
    chave_acesso_esperada: str | None = Field(
        default=None,
        description="Expected 44-digit NFC-e access key, when the receipt shows one.",
    )
    itens_esperados: list[ItemEsperado]


class CatalogoFixture(BaseModel):
    """The fixed catalog and categories a case is scored against.

    Small enough to be matched in a single AI call (``proximoCursor`` absent), so
    the evaluation exercises the no-search path of ``ExtracaoService`` by default.
    """

    insumos: list[CatalogoItem]
    categorias: list[Categoria]


class Manifesto(BaseModel):
    """The full versioned evaluation set loaded from a JSON file.

    ``threshold`` is the per-item accuracy bar a provider must clear before a
    swap. ``respostas_fake`` maps a case id to the raw
    JSON a provider would return, so tests replay them through the fake provider
    and never touch a real one. Real runs ignore this field.
    """

    versao: str = Field(description="Set version, bumped on any case or fixture change.")
    threshold: Decimal = Field(default=Decimal("0.90"), gt=0, le=1)
    catalogo: CatalogoFixture
    casos: list[CasoAvaliacao]
    respostas_fake: dict[str, str] = Field(default_factory=dict)
