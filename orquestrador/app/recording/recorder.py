"""Item-by-item recording of a confirmed purchase.

Each recordable item is recorded on its own, so one failure never blocks the
others. New supplies are created with get-or-create BEFORE their
movement, because the idempotency key needs the supply id. The key is
`nota:{chave}:{insumoId}` when the purchase has an invoice key, otherwise
`fluxo:{fluxoId}:{insumoId}`. Every item's isolated status is
written to Redis, so a restart can tell done from pending.

The automatic retry worker is wired through `RetrySink`: each
retryable item is handed to the sink as a `RetryEnqueue`, carrying the draft so
the worker can replay the exact recording path with the same key. The default
sink is a no-op, so without the worker the retryable items are only reported to
the owner with the retry offer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.errors import CoreError, CoreNotFoundError, CoreTransientError
from app.core.models import (
    MovimentoRequest,
    ObterOuCriarRequest,
)
from app.flow.models import Draft, DraftItem
from app.flow.ports import StatePort
from app.llm.schemas import AcaoItem
from app.recording.models import FailureKind, ItemResult, ItemStatus, RecordingResult


@dataclass(frozen=True)
class RetryEnqueue:
    """What the recorder hands the retry seam for one retryable item.

    It carries the item outcome plus the draft and the single draft item, so the
    retry worker can replay the exact recording path and reuse the deterministic
    idempotency key."""

    result: ItemResult
    draft: Draft
    item: DraftItem


# A retryable item is handed to this sink for the retry worker.
RetrySink = Callable[[RetryEnqueue], Awaitable[None]]


async def no_op_retry_sink(_enqueue: RetryEnqueue) -> None:
    """Default sink: the retry worker is not wired (opt-in)."""


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
                await self._retry_sink(RetryEnqueue(result=resultado, draft=draft, item=item))
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
        falha = _failure_kind(exc) if retryable else None
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
            falha=falha,
        )

    async def _persist(self, fluxo_id: str, resultado: ItemResult) -> None:
        await self._state.set_item_status(
            fluxo_id, _item_key(resultado.index), resultado.model_dump(mode="json")
        )


def _motivo(exc: CoreError, retryable: bool) -> str:
    if not retryable:
        return "não passou na validação"
    if isinstance(exc, CoreNotFoundError):
        return "o insumo não está disponível"
    return "o sistema estava fora do ar"


def _failure_kind(exc: CoreError) -> FailureKind:
    if isinstance(exc, CoreNotFoundError):
        return FailureKind.SUPPLY_MISSING
    return FailureKind.TRANSIENT
