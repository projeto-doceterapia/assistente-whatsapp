"""Item-by-item recording of a confirmed purchase.

Each recordable item is recorded on its own, so one failure never blocks the
others. New supplies are created with get-or-create BEFORE their
movement, because the idempotency key needs the supply id. The key is
`nota:{chave}:{insumoId}` when the purchase has an invoice key, otherwise
`fluxo:{fluxoId}:{insumoId}`. Every item's isolated status is
written to Redis, so a restart can tell done from pending.

The automatic retry worker is a separate seam; here `RetrySink` is only that
seam. The default is a no-op, so retryable items are reported to the owner with
the retry offer and nothing is enqueued yet.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.errors import CoreError, CoreNotFoundError, CoreTransientError
from app.core.models import (
    MovimentoRequest,
    ObterOuCriarRequest,
)
from app.flow.models import Draft, DraftItem
from app.flow.ports import StatePort
from app.llm.schemas import AcaoItem
from app.recording.models import ItemResult, ItemStatus, RecordingResult

# A retryable item is handed to this sink for the retry worker.
RetrySink = Callable[[ItemResult], Awaitable[None]]


async def no_op_retry_sink(_item: ItemResult) -> None:
    """Default sink: the retry worker does not exist yet."""


def _item_key(index: int) -> str:
    return f"item:{index}"


def idempotency_key(draft: Draft, insumo_id: int) -> str:
    """Build the per-item idempotency key."""
    if draft.chave_acesso is not None:
        return f"nota:{draft.chave_acesso}:{insumo_id}"
    return f"fluxo:{draft.fluxo_id}:{insumo_id}"


class Recorder:
    """Records a confirmed draft one item at a time."""

    def __init__(
        self,
        state: StatePort,
        core,
        retry_sink: RetrySink = no_op_retry_sink,
    ) -> None:
        self._state = state
        self._core = core
        self._retry_sink = retry_sink

    async def record(self, draft: Draft) -> RecordingResult:
        resultados: list[ItemResult] = []
        for item in draft.recordable_itens:
            resultado = await self._record_item(draft, item)
            await self._persist(draft.fluxo_id, resultado)
            if resultado.retryable:
                await self._retry_sink(resultado)
            resultados.append(resultado)
        return RecordingResult(itens=resultados)

    async def _record_item(self, draft: Draft, item: DraftItem) -> ItemResult:
        # An item already OK in Redis (a restart mid-recording) is not re-sent;
        # the movement call is idempotent anyway, but this avoids the round trip.
        existing = await self._state.get_item_status(draft.fluxo_id, _item_key(item.index))
        if existing and existing.get("status") == ItemStatus.OK.value:
            return ItemResult.model_validate(existing)

        try:
            insumo_id = await self._resolve_insumo(item)
        except CoreError as exc:
            return self._failure(draft, item, insumo_id=None, exc=exc)

        key = idempotency_key(draft, insumo_id)
        request = MovimentoRequest(
            insumoId=insumo_id,
            quantidadeInformada=item.quantidade_total,
            unidadeInformada=item.unidade,
            origem=draft.origem,
            valorLiquido=item.valor_liquido,
            chaveNota=draft.chave_acesso,
            fluxoId=draft.fluxo_id,
        )
        try:
            await self._core.registrar_movimento(key, request)
        except CoreError as exc:
            return self._failure(draft, item, insumo_id=insumo_id, exc=exc, key=key)

        return ItemResult(
            index=item.index,
            nome=item.nome,
            calculo=item.calculo or None,
            status=ItemStatus.OK,
            insumo_id=insumo_id,
            idempotency_key=key,
        )

    async def _resolve_insumo(self, item: DraftItem) -> int:
        if item.acao is AcaoItem.CRIAR:
            # Get-or-create runs before the movement so the key has a real id.
            resultado = await self._core.obter_ou_criar_insumo(
                ObterOuCriarRequest(
                    nome=item.nome,
                    unidade=item.unidade,
                    fkCategoriaInsumo=item.categoria_id,
                )
            )
            return resultado.insumo.idInsumo
        assert item.insumo_id is not None  # ATUALIZAR items always carry an id
        return item.insumo_id

    def _failure(
        self,
        draft: Draft,
        item: DraftItem,
        *,
        insumo_id: int | None,
        exc: CoreError,
        key: str | None = None,
    ) -> ItemResult:
        # Transient errors and a missing/inactive supply can be retried later;
        # validation and conflicts are definitive.
        retryable = isinstance(exc, (CoreTransientError, CoreNotFoundError))
        status = ItemStatus.RETRYABLE if retryable else ItemStatus.FAILED
        resolved_key = key or (
            idempotency_key(draft, insumo_id) if insumo_id is not None else ""
        )
        return ItemResult(
            index=item.index,
            nome=item.nome,
            calculo=item.calculo or None,
            status=status,
            insumo_id=insumo_id,
            idempotency_key=resolved_key,
            motivo=_motivo(exc, retryable),
        )

    async def _persist(self, fluxo_id: str, resultado: ItemResult) -> None:
        await self._state.set_item_status(
            fluxo_id, _item_key(resultado.index), resultado.model_dump(mode="json")
        )


def _motivo(exc: CoreError, retryable: bool) -> str:
    if retryable:
        return "o sistema estava fora do ar"
    return "não passou na validação"
