from __future__ import annotations

from dataclasses import dataclass

from app.core.client import CoreClient
from app.core.models import CatalogoItem, CatalogoPagina
from app.llm.port import ImageInput, LlmPort, LlmRequest
from app.llm.prompts import PROMPT_EXTRACAO
from app.llm.schemas import AcaoItem, ExtracaoResultado
from app.purchase.rendering import Categoria, render_contexto


@dataclass(frozen=True)
class ExtracaoConfig:
    """Search-loop limits.

    ``rodadas_busca`` caps the partial-catalog search rounds (default 1), and
    ``limite_busca`` caps how many candidates each `?q=` search returns.
    """

    rodadas_busca: int = 1
    limite_busca: int = 200


def _itens_sem_match(resultado: ExtracaoResultado) -> list:
    return [
        item
        for item in resultado.itens
        if item.acao is AcaoItem.ESCLARECER and item.termos_busca
    ]


async def _buscar_candidatos(
    core: CoreClient,
    termos: list[str],
    limite: int,
) -> list[CatalogoItem]:
    # One `?q=` search per term, candidates merged and deduped by id.
    vistos: dict[int, CatalogoItem] = {}
    for termo in termos:
        pagina = await core.listar_catalogo(q=termo, limite=limite)
        for item in pagina.itens:
            vistos.setdefault(item.idInsumo, item)
    return list(vistos.values())


class ExtracaoService:
    """Runs extraction with the partial-catalog search loop.

    A small catalog (``proximoCursor is None``) is matched in a single AI call.
    When the catalog is partial, the AI returns ``termos_busca`` for unmatched
    items; the orchestrator runs one `?q=` search per term, dedups the
    candidates, and asks the AI to match only those items against them, capped by
    ``rodadas_busca``. Items still unmatched stay as create or clarify, never
    matched to an invented id.
    """

    def __init__(
        self,
        llm: LlmPort,
        core: CoreClient,
        config: ExtracaoConfig | None = None,
    ) -> None:
        self._llm = llm
        self._core = core
        self._config = config or ExtracaoConfig()

    async def extrair(
        self,
        pagina: CatalogoPagina,
        categorias: list[Categoria],
        text: str | None = None,
        image: ImageInput | None = None,
    ) -> ExtracaoResultado:
        parcial = pagina.proximoCursor is not None
        contexto = render_contexto(pagina.itens, categorias, pagina.total, parcial)
        system = PROMPT_EXTRACAO.render(contexto)
        resultado = await self._llm.generate(
            LlmRequest(system=system, text=text, image=image), ExtracaoResultado
        )

        if not parcial:
            return resultado

        rodadas = 0
        while rodadas < self._config.rodadas_busca:
            pendentes = _itens_sem_match(resultado)
            if not pendentes:
                break
            termos = [termo for item in pendentes for termo in item.termos_busca]
            candidatos = await _buscar_candidatos(
                self._core, termos, self._config.limite_busca
            )
            if not candidatos:
                break
            contexto_busca = render_contexto(
                candidatos, categorias, len(candidatos), parcial=False
            )
            system_busca = PROMPT_EXTRACAO.render(contexto_busca)
            resultado = await self._llm.generate(
                LlmRequest(system=system_busca, text=text, image=image),
                ExtracaoResultado,
            )
            rodadas += 1

        return resultado
