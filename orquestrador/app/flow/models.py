"""Data the flow carries between steps and persists in the conversation hash.

The draft is the frozen understanding of a purchase while it awaits
confirmation. It is JSON-serializable because it lives in the Redis
conversation hash between inbound messages. Nothing here performs
I/O; the handler and steps own that.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field

from app.channel.port import ChannelPort
from app.core.client import CoreClient
from app.core.models import OrigemMovimentacao, UnidadeMedida
from app.flow.ports import ClassifierPort, StatePort
from app.llm.port import ImageInput, LlmPort
from app.llm.retry import RetrySettings, Sleeper
from app.llm.schemas import AcaoItem, ItemExtraido
from app.media.preprocess import PreprocessConfig
from app.purchase.extracao import ExtracaoService
from app.purchase.rendering import Categoria
from app.purchase.validacao import ItemValidado

# The current supply categories are read from the core (GET /categorias-insumo).
# The core client does not expose that route yet, so the flow takes
# a provider seam: production wires the real call, tests pass a fixed list.
CategoriasProvider = Callable[[], Awaitable[list[Categoria]]]


class DraftItem(BaseModel):
    """One confirmed-pending purchase line, ready to record.

    It keeps everything the recording needs (name, unit, quantity, cost,
    category for new supplies) plus the display calculation for messages. The
    index is stable within the draft and feeds the per-item idempotency key.
    """

    index: int
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

    @classmethod
    def from_validado(cls, index: int, item: ItemValidado) -> "DraftItem":
        return cls(
            index=index,
            acao=item.acao,
            insumo_id=item.insumo_id,
            nome=item.nome,
            unidade=item.unidade,
            quantidade_total=item.quantidade_total,
            calculo=item.calculo,
            valor_liquido=item.valor_liquido,
            custo_unitario=item.custo_unitario,
            categoria_id=item.categoria_id,
            categoria_nome=item.categoria_nome,
        )

    @property
    def recordable(self) -> bool:
        """Whether this line can be recorded (not awaiting clarification)."""
        return self.acao is not AcaoItem.ESCLARECER


class Draft(BaseModel):
    """The whole pending purchase held between messages.

    `fluxo_id` identifies the flow for idempotency keys and item status.
    `chave_acesso` is the validated invoice key when the receipt had one.
    `itens` are the consolidated, validated lines shown and recorded.
    `raw_itens` keeps the AI's raw reading so a free-text adjustment can be
    merged and re-validated deterministically. `ajustes` counts the
    free-text adjustments already applied.
    """

    fluxo_id: str
    chave_acesso: str | None = None
    origem: OrigemMovimentacao = OrigemMovimentacao.TEXTO
    itens: list[DraftItem]
    raw_itens: list[ItemExtraido] = Field(default_factory=list)
    ajustes: int = 0

    @property
    def recordable_itens(self) -> list[DraftItem]:
        return [item for item in self.itens if item.recordable]


@dataclass(frozen=True)
class FlowDeps:
    """Everything the handler and steps need, injected once.

    Grouping the collaborators keeps the handler small: it wires nothing, it
    only orchestrates. `beneficiaria` is the one authorized sender;
    messages from any other sender are dropped.
    """

    state: StatePort
    channel: ChannelPort
    core: CoreClient
    llm: LlmPort
    classifier: ClassifierPort
    extracao: ExtracaoService
    beneficiaria: str
    categorias_provider: CategoriasProvider
    max_ajustes: int = 2
    preprocess_config: PreprocessConfig | None = None
    llm_retry: RetrySettings | None = None
    llm_retry_sleep: Sleeper | None = None

    async def categorias(self) -> list[Categoria]:
        return await self.categorias_provider()

    def image_input(self, data: bytes, mime: str) -> ImageInput:
        return ImageInput(data=data, mime_type=mime)


class ConfirmationOutcome(BaseModel):
    """Result of building the confirmation for an extracted purchase.

    `message` is the PT-BR text to send. `draft` is the draft to persist, or
    None when the flow must end immediately (a fully duplicated invoice), in
    which case the caller does not enter AWAITING_CONFIRMATION.
    """

    message: str
    draft: Draft | None
    ended: bool = False
