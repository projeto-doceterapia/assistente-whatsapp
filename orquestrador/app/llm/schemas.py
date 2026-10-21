from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from app.core.models import UnidadeMedida


class AcaoItem(str, Enum):
    """What the AI proposes to do with an extracted line.

    ``ATUALIZAR`` matches an existing supply, ``CRIAR`` proposes a new supply,
    and ``ESCLARECER`` means the line could not be understood well enough to act.
    """

    ATUALIZAR = "ATUALIZAR"
    CRIAR = "CRIAR"
    ESCLARECER = "ESCLARECER"


class ItemExtraido(BaseModel):
    """One purchase line as the AI reads it from the receipt or text.

    Package fields are RAW facts as printed on the receipt; the orchestrator
    computes the total quantity and the net unit cost in code.
    The AI never does arithmetic. ``valor_liquido`` is the line value after any
    item-level discount. ``categoria_id`` is only meaningful when ``acao`` is
    ``CRIAR``. ``termos_busca`` carries search terms for a partial catalog, to be
    run by the orchestrator; it is empty when the item was matched.
    """

    acao: AcaoItem
    insumo_id: int | None = Field(
        default=None,
        description="Catalog id when the line matches an existing supply (acao=ATUALIZAR).",
    )
    nome_extraido: str = Field(
        description="Supply name as read on the receipt or text, trimmed and short.",
    )
    quantidade_embalagens: Decimal = Field(
        description="How many packages were bought, as printed (e.g. 2 for '2 x 5 kg').",
    )
    tamanho_embalagem: Decimal = Field(
        description="Size of one package in unidade_embalagem (e.g. 5 for '2 x 5 kg').",
    )
    unidade_embalagem: UnidadeMedida = Field(
        description="Unit of the package size as printed (KG, G, L, ML or UNIDADE).",
    )
    unidade: UnidadeMedida | None = Field(
        default=None,
        description="Hint for the supply unit the orchestrator should record in.",
    )
    valor_liquido: Decimal | None = Field(
        default=None,
        description="Net line value after item discount, when a price is present.",
    )
    categoria_id: int | None = Field(
        default=None,
        description="Chosen category id for a new supply (acao=CRIAR); validated in code.",
    )
    termos_busca: list[str] = Field(
        default_factory=list,
        description="Search terms for the orchestrator to query a partial catalog.",
    )


class ExtracaoResultado(BaseModel):
    """Full AI extraction of one purchase message.

    ``chave_acesso`` is the 44-digit NFC-e access key when the AI could read it;
    the orchestrator still validates it (modulo-11) and prefers a decoded QR/
    barcode. ``itens`` holds every line the AI identified.
    """

    itens: list[ItemExtraido]
    chave_acesso: str | None = Field(
        default=None,
        description="Access key the AI read from the receipt, if any; validated in code.",
    )


class AjusteItem(BaseModel):
    """One change the AI understood from a free-text correction.

    References the draft line by ``nome_extraido`` or ``insumo_id``; any field
    left as ``None`` keeps its previous value in the draft.
    """

    insumo_id: int | None = None
    nome_extraido: str | None = None
    acao: AcaoItem | None = None
    quantidade_embalagens: Decimal | None = None
    tamanho_embalagem: Decimal | None = None
    unidade_embalagem: UnidadeMedida | None = None
    unidade: UnidadeMedida | None = None
    valor_liquido: Decimal | None = None
    categoria_id: int | None = None
    remover: bool = Field(
        default=False,
        description="True when the owner asked to drop this line from the draft.",
    )


class AjusteResultado(BaseModel):
    """AI interpretation of a free-text adjustment over an existing draft."""

    itens: list[AjusteItem]


class TipoIntencao(str, Enum):
    """High-level intent of a free-text reply during a flow."""

    CONFIRMAR = "CONFIRMAR"
    AJUSTAR = "AJUSTAR"
    CANCELAR = "CANCELAR"
    TENTAR_NOVAMENTE = "TENTAR_NOVAMENTE"
    NOVA_COMPRA = "NOVA_COMPRA"
    OUTRO = "OUTRO"


class IntencaoResultado(BaseModel):
    """AI classification of a short reply into one intent."""

    intencao: TipoIntencao
