from __future__ import annotations

from typing import Protocol


class AlertRaiser(Protocol):
    """Port for raising an operational alert. The concrete implementation
    (creating an ALERTA_OPERACIONAL notification through the core)
    is wired by `main`; the delivery worker only depends on this port so it does
    not reach outside its package. Injecting None disables alerting."""

    async def raise_alert(self, mensagem: str) -> None:
        ...
