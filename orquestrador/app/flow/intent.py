"""Reply-intent classification during a pending flow.

The LLM returns `TipoIntencao` for a short reply; this module maps it onto the
flow's `ReplyIntent` and wraps the LLM behind a small classifier so the handler
depends on an intent, not on prompts or the provider.
"""

from __future__ import annotations

from enum import Enum

from app.llm.port import LlmPort, LlmRequest
from app.llm.prompts import PROMPT_INTENCAO
from app.llm.schemas import IntencaoResultado, TipoIntencao


class ReplyIntent(str, Enum):
    """What a free-text reply means while a draft is awaiting confirmation."""

    CONFIRM = "CONFIRM"
    ADJUST = "ADJUST"
    CANCEL = "CANCEL"
    NEW_PURCHASE = "NEW_PURCHASE"
    OTHER = "OTHER"


_FROM_LLM: dict[TipoIntencao, ReplyIntent] = {
    TipoIntencao.CONFIRMAR: ReplyIntent.CONFIRM,
    TipoIntencao.AJUSTAR: ReplyIntent.ADJUST,
    TipoIntencao.CANCELAR: ReplyIntent.CANCEL,
    TipoIntencao.NOVA_COMPRA: ReplyIntent.NEW_PURCHASE,
    # A standalone "try again" has no meaning while confirming a draft (the
    # retry flow is task 10.3), so it reads as an unrelated reply here.
    TipoIntencao.TENTAR_NOVAMENTE: ReplyIntent.OTHER,
    TipoIntencao.OUTRO: ReplyIntent.OTHER,
}


class LlmClassifier:
    """Classifier backed by the LLM intent prompt."""

    def __init__(self, llm: LlmPort) -> None:
        self._llm = llm

    async def classify(self, text: str) -> ReplyIntent:
        resultado = await self._llm.generate(
            LlmRequest(system=PROMPT_INTENCAO.render(), text=text), IntencaoResultado
        )
        return _FROM_LLM.get(resultado.intencao, ReplyIntent.OTHER)
