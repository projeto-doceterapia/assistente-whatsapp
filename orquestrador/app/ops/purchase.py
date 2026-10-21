"""COMPRA_REGISTRADA in-app record.

After each fully or partially recorded purchase, the orchestrator creates one
`COMPRA_REGISTRADA` notification through `POST /notificacoes`. It is in-app only
(the core defaults it to `NAO_ENVIAR`), so it never reaches WhatsApp.

`on_purchase_recorded(summary)` is the hook: pass it as the recorder's purchase
listener, or call it right after `Recorder.record(draft)` returns. It is a best
effort — a recorded purchase is never rolled back because its in-app notice
failed, so a core error here is logged and swallowed.
"""

from __future__ import annotations

import logging

from app.core.client import CoreClient
from app.core.errors import CoreError
from app.core.models import NotificacaoRequest, TipoNotificacao
from app.recording.models import RecordingResult

logger = logging.getLogger("ops.purchase")

_TITULO = "Compra registrada"


class PurchaseNotifier:
    """Creates the in-app COMPRA_REGISTRADA notice from a recording result."""

    def __init__(self, core: CoreClient) -> None:
        self._core = core

    async def on_purchase_recorded(self, summary: RecordingResult) -> None:
        if not summary.itens:
            return  # nothing was recordable: no purchase to record
        if summary.none_ok:
            return  # nothing recorded (all items failed): not a registered purchase
        try:
            await self._core.criar_notificacao(
                NotificacaoRequest(
                    tipo=TipoNotificacao.COMPRA_REGISTRADA,
                    titulo=_TITULO,
                    mensagem=_mensagem(summary),
                )
            )
        except CoreError:
            logger.warning("compra_registrada_in_app_failed")


def _mensagem(summary: RecordingResult) -> str:
    registrados = len(summary.recorded)
    total = len(summary.itens)
    if summary.all_ok:
        return f"Compra registrada com {registrados} {_itens(registrados)}."
    falhas = total - registrados
    return (
        f"Compra registrada parcialmente: {registrados} {_itens(registrados)} "
        f"registrado(s) e {falhas} pendente(s)."
    )


def _itens(n: int) -> str:
    return "item" if n == 1 else "itens"
