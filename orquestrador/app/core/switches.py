"""The assistant switches, read from the core with a short cache.

The flow asks on every message, so the value is kept for a few seconds instead of
calling the core each time. When the core cannot answer, the last value stands;
with no value yet the assistant stays active, so a core outage at startup does
not silence it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from app.core.client import CoreClient
from app.core.errors import CoreError
from app.delivery.clock import Clock, SystemClock

logger = logging.getLogger("core.switches")


@dataclass(frozen=True)
class Switches:
    avisos_automaticos_ativo: bool = True
    assistente_ativo: bool = True

    @property
    def envia_avisos(self) -> bool:
        """Notices reach WhatsApp only with both switches on."""
        return self.avisos_automaticos_ativo and self.assistente_ativo


class AssistantSwitches:
    TTL_S = 30

    def __init__(self, core: CoreClient, clock: Clock | None = None) -> None:
        self._core = core
        self._clock = clock or SystemClock()
        self._last = Switches()
        self._read_at: datetime | None = None

    async def current(self) -> Switches:
        now = self._clock.now()
        if self._read_at is not None and (now - self._read_at).total_seconds() < self.TTL_S:
            return self._last
        # A failed read restarts the TTL too, so a core outage is not retried on
        # every message. ValueError covers a body that is not the expected JSON.
        self._read_at = now
        try:
            lidos = await self._core.obter_interruptores()
        except (CoreError, ValueError):
            logger.warning("switches_read_failed")
            return self._last
        self._last = Switches(
            avisos_automaticos_ativo=lidos.avisosAutomaticosAtivo,
            assistente_ativo=lidos.assistenteAtivo,
        )
        return self._last
