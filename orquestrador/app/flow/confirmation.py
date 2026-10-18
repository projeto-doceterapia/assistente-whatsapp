"""Confirmation step: invoice status, duplicate decision and the message.

When the purchase has an access key, the step checks the core before asking for
confirmation: a fully recorded invoice ends the flow with the
already-registered message; a partially recorded one drops the recorded items
and confirms only the rest. Without a key, or for an INEXISTENTE one, it renders
the normal confirmation. Message text is PT-BR, built by the message catalog.
"""

from __future__ import annotations

from app.core.models import StatusNotaFiscal
from app.flow.models import ConfirmationOutcome, Draft, DraftItem, FlowDeps
from app.llm.schemas import AcaoItem
from app.messages import (
    ConfirmationItem,
    ItemMarker,
    invoice_already_registered,
    invoice_partial,
    render_confirmation,
)

_MARKER_BY_ACAO = {
    AcaoItem.ATUALIZAR: ItemMarker.CONFIRM,
    AcaoItem.CRIAR: ItemMarker.NEW,
    AcaoItem.ESCLARECER: ItemMarker.CLARIFY,
}


class ConfirmationStep:
    """Builds the confirmation, consulting the invoice status when a key exists."""

    def __init__(self, deps: FlowDeps) -> None:
        self._deps = deps

    async def build(self, draft: Draft) -> ConfirmationOutcome:
        if draft.chave_acesso is None:
            return ConfirmationOutcome(
                message=render_confirmation(_linhas(draft.itens)), draft=draft
            )
        return await self._with_invoice(draft)

    async def _with_invoice(self, draft: Draft) -> ConfirmationOutcome:
        status = await self._deps.core.consultar_nota(draft.chave_acesso)
        if status.status is StatusNotaFiscal.INEXISTENTE:
            return ConfirmationOutcome(
                message=render_confirmation(_linhas(draft.itens)), draft=draft
            )

        ja_gravados = set(status.insumoIds)
        pendentes = [
            item
            for item in draft.itens
            if not (item.acao is AcaoItem.ATUALIZAR and item.insumo_id in ja_gravados)
        ]
        if not _tem_gravavel(pendentes):
            # Every extracted supply is already recorded for this key.
            return ConfirmationOutcome(
                message=invoice_already_registered(), draft=None, ended=True
            )

        parcial = draft.model_copy(update={"itens": _reindex(pendentes)})
        return ConfirmationOutcome(
            message=invoice_partial(_linhas(parcial.itens)), draft=parcial
        )


def _linhas(itens: list[DraftItem]) -> list[ConfirmationItem]:
    return [
        ConfirmationItem(
            nome=item.nome,
            marker=_MARKER_BY_ACAO[item.acao],
            calculo=item.calculo or None,
            categoria=item.categoria_nome,
        )
        for item in itens
    ]


def _tem_gravavel(itens: list[DraftItem]) -> bool:
    return any(item.recordable for item in itens)


def _reindex(itens: list[DraftItem]) -> list[DraftItem]:
    return [item.model_copy(update={"index": i}) for i, item in enumerate(itens)]
