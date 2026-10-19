"""Supply-categories provider for the flow (seam wired by `main`).

`FlowDeps.categorias_provider` must return the current supply categories so the
AI can classify new supplies. This provider reads them from the core
(`GET /categorias-insumo`) with a short in-process TTL cache, so a burst of
messages does not hit the core every time, and a safe fallback: when the core
is unavailable and nothing is cached, it returns the single default category
'A classificar' so extraction still proceeds (CATEGORIA_PADRAO).
"""

from __future__ import annotations

import logging
import time

from app.core.client import CoreClient
from app.core.errors import CoreError
from app.purchase.rendering import CATEGORIA_PADRAO, Categoria

logger = logging.getLogger("ops.categorias")

# Sentinel id for the synthesized default category. It is never sent to the
# core as a real fkCategoriaInsumo; the flow treats an unknown/default category
# as "classify later", matching the core's own default handling.
_DEFAULT_CATEGORIA_ID = 0


class CategoriasProvider:
    """Cached provider with a safe fallback. `monotonic` is injected so tests
    control cache expiry without sleeping."""

    def __init__(
        self,
        core: CoreClient,
        *,
        ttl_s: int = 300,
        monotonic=time.monotonic,
    ) -> None:
        self._core = core
        self._ttl_s = ttl_s
        self._monotonic = monotonic
        self._cache: list[Categoria] | None = None
        self._fetched_at = 0.0

    async def __call__(self) -> list[Categoria]:
        now = self._monotonic()
        if self._cache is not None and (now - self._fetched_at) < self._ttl_s:
            return self._cache
        try:
            categorias = [
                Categoria(idCategoriaInsumo=c.idCategoriaInsumo, nome=c.nome)
                for c in await self._core.listar_categorias()
            ]
        except CoreError:
            logger.warning("categorias_core_unavailable")
            # Keep serving a stale cache if we have one; otherwise fall back to
            # the default category so the AI can still classify as "classify later".
            if self._cache is not None:
                return self._cache
            return [_default_categoria()]
        self._cache = categorias
        self._fetched_at = now
        return categorias


def _default_categoria() -> Categoria:
    return Categoria(idCategoriaInsumo=_DEFAULT_CATEGORIA_ID, nome=CATEGORIA_PADRAO)
