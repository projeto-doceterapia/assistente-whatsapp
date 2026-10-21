"""Adjustment step: a free-text correction revises the draft.

The AI reads the correction into `AjusteResultado` (only the changed fields).
The step merges those changes onto the draft's raw lines, drops removed lines,
then re-validates and re-consolidates in code so quantities and the calculation
stay deterministic. The adjustment count lives in the draft; the
state machine enforces the limit.
"""

from __future__ import annotations

from app.flow.extraction import validar_draft
from app.flow.models import Draft, FlowDeps
from app.llm.port import LlmRequest
from app.llm.prompts import PROMPT_AJUSTE
from app.llm.schemas import AjusteItem, AjusteResultado, ItemExtraido


class AdjustmentStep:
    """Applies one free-text adjustment and returns the revised draft."""

    def __init__(self, deps: FlowDeps) -> None:
        self._deps = deps

    async def apply(self, draft: Draft, text: str) -> Draft:
        ajuste = await self._deps.llm.generate(
            LlmRequest(system=PROMPT_AJUSTE.render(), text=text), AjusteResultado
        )
        novos_raw = _merge(draft.raw_itens, ajuste.itens)

        categorias = await self._deps.categorias()
        pagina = await self._deps.core.listar_catalogo()
        itens = validar_draft(novos_raw, pagina, categorias)

        return draft.model_copy(
            update={
                "itens": itens,
                "raw_itens": novos_raw,
                "ajustes": draft.ajustes + 1,
            }
        )


def _merge(
    raw: list[ItemExtraido], ajustes: list[AjusteItem]
) -> list[ItemExtraido]:
    atuais = [item.model_copy(deep=True) for item in raw]
    for ajuste in ajustes:
        alvo = _match(atuais, ajuste)
        if alvo is None:
            continue
        if ajuste.remover:
            atuais.remove(alvo)
            continue
        _aplicar(alvo, ajuste)
    return atuais


def _match(itens: list[ItemExtraido], ajuste: AjusteItem) -> ItemExtraido | None:
    if ajuste.insumo_id is not None:
        for item in itens:
            if item.insumo_id == ajuste.insumo_id:
                return item
    if ajuste.nome_extraido is not None:
        alvo = ajuste.nome_extraido.strip().casefold()
        for item in itens:
            if item.nome_extraido.strip().casefold() == alvo:
                return item
    return None


def _aplicar(item: ItemExtraido, ajuste: AjusteItem) -> None:
    # Only the fields the AI filled override the draft; the rest stay.
    campos = ajuste.model_dump(exclude_none=True, exclude={"remover", "nome_extraido"})
    for nome, valor in campos.items():
        setattr(item, nome, valor)
