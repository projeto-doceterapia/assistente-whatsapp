from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import quantiles

from app.core.client import CoreClient
from app.core.models import CatalogoPagina
from app.core.stub import CoreStub
from app.evaluation.manifest import CasoAvaliacao, Manifesto, TipoEntrada
from app.evaluation.providers import (
    ProviderError,
    ReportaUso,
    construir_provider,
    fake_para_resposta,
)
from app.evaluation.report import CasoResultado, Relatorio, Uso
from app.evaluation.scoring import pontuar_caso
from app.llm.port import ImageInput, LlmPort
from app.purchase.extracao import ExtracaoService
from app.purchase.rendering import Categoria
from app.purchase.validacao import ItemValidado, consolidar, validar_item


class RunnerError(RuntimeError):
    """Raised when the run cannot proceed (bad input, missing image, no provider)."""


def _percentil(valores: list[float], pct: float) -> float:
    if not valores:
        return 0.0
    if len(valores) == 1:
        return valores[0]
    # statistics.quantiles with n=100 gives percentile cut points; clamp the index.
    cortes = quantiles(valores, n=100, method="inclusive")
    indice = min(max(int(round(pct)) - 1, 0), len(cortes) - 1)
    return cortes[indice]


async def _computar_caso(
    service: ExtracaoService,
    pagina: CatalogoPagina,
    categorias: list[Categoria],
    caso: CasoAvaliacao,
    base_imagens: Path,
) -> tuple[list[ItemValidado], float]:
    text = caso.texto if caso.tipo is TipoEntrada.TEXTO else None
    image: ImageInput | None = None
    if caso.tipo is TipoEntrada.IMAGEM:
        if not caso.imagem:
            raise RunnerError(f"case {caso.id}: image case without a path")
        caminho = base_imagens / caso.imagem
        if not caminho.is_file():
            raise RunnerError(f"case {caso.id}: image not found: {caminho}")
        image = ImageInput(data=caminho.read_bytes())

    inicio = time.perf_counter()
    resultado = await service.extrair(pagina, categorias, text=text, image=image)
    decorrido_ms = (time.perf_counter() - inicio) * 1000

    catalogo = {i.idInsumo: i for i in pagina.itens}
    cat_por_id = {c.idCategoriaInsumo: c for c in categorias}
    validados = [validar_item(item, catalogo, cat_por_id) for item in resultado.itens]
    return consolidar(validados), decorrido_ms


def _agregar_uso(provider: LlmPort) -> Uso:
    if not isinstance(provider, ReportaUso) or not provider.usos:
        return Uso(
            disponivel=False,
            motivo="o provedor não reporta uso de tokens nesta execução",
        )
    prompt = sum(u.get("prompt_tokens", 0) for u in provider.usos)
    output = sum(u.get("output_tokens", 0) for u in provider.usos)
    total = sum(u.get("total_tokens", 0) for u in provider.usos)
    return Uso(
        disponivel=True,
        prompt_tokens=prompt,
        output_tokens=output,
        total_tokens=total,
    )


async def executar(
    manifesto: Manifesto,
    base_imagens: Path,
    *,
    usar_fake: bool,
    provider_nome: str,
    threshold: Decimal | None = None,
) -> Relatorio:
    """Run the full evaluation set and build a report.

    With ``usar_fake`` the runner replays the manifest's recorded responses
    through the fake provider (offline, deterministic, used by tests). Otherwise
    it builds the named real provider from the environment and runs the real
    pipeline. A provider error (e.g. a missing ``GEMINI_API_KEY``) is reported as
    an observation rather than raised, so a text run can report that Gemini could
    not be reached without crashing.
    """
    limite = threshold if threshold is not None else manifesto.threshold
    categorias = manifesto.catalogo.categorias
    pagina = CatalogoPagina(
        itens=manifesto.catalogo.insumos,
        total=len(manifesto.catalogo.insumos),
        proximoCursor=None,
    )
    observacoes: list[str] = []

    provider_compartilhado: LlmPort | None = None
    if not usar_fake:
        try:
            provider_compartilhado = construir_provider(provider_nome)
        except ProviderError as exc:
            observacoes.append(
                f"provedor '{provider_nome}' indisponível: {exc}. "
                "Nenhuma chamada real foi feita."
            )

    casos_resultado: list[CasoResultado] = []
    latencias: list[float] = []
    total_acertos = 0
    total_denominador = 0
    total_esperados = 0
    acertos_categoria = 0
    categorias_esperadas = 0

    stub = CoreStub()
    async with stub.client() as http:
        core = CoreClient(http)
        for caso in manifesto.casos:
            if usar_fake:
                recorded = manifesto.respostas_fake.get(caso.id)
                if recorded is None:
                    casos_resultado.append(
                        _caso_com_erro(caso, "sem resposta fake gravada para o caso")
                    )
                    total_esperados += len(caso.itens_esperados)
                    total_denominador += len(caso.itens_esperados)
                    continue
                provider: LlmPort = fake_para_resposta(recorded)
            elif provider_compartilhado is not None:
                provider = provider_compartilhado
            else:
                casos_resultado.append(_caso_com_erro(caso, "provedor indisponível"))
                total_esperados += len(caso.itens_esperados)
                total_denominador += len(caso.itens_esperados)
                continue

            service = ExtracaoService(provider, core)
            try:
                validados, latencia_ms = await _computar_caso(
                    service, pagina, categorias, caso, base_imagens
                )
            except Exception as exc:  # noqa: BLE001 - isolate per-case failures in the report
                casos_resultado.append(_caso_com_erro(caso, str(exc)))
                total_esperados += len(caso.itens_esperados)
                total_denominador += len(caso.itens_esperados)
                continue

            pontuacao = pontuar_caso(caso.itens_esperados, validados)
            latencias.append(latencia_ms)
            total_acertos += pontuacao.acertos
            total_denominador += pontuacao.denominador
            total_esperados += pontuacao.esperados
            acertos_categoria += pontuacao.acertos_categoria
            categorias_esperadas += pontuacao.categorias_esperadas
            casos_resultado.append(
                CasoResultado(
                    id=caso.id,
                    tipo=caso.tipo.value,
                    itens_esperados=pontuacao.esperados,
                    itens_computados=pontuacao.computados,
                    acertos=pontuacao.acertos,
                    denominador=pontuacao.denominador,
                    latencia_ms=round(latencia_ms, 2),
                    itens=pontuacao.itens,
                )
            )

    acuracia = (total_acertos / total_denominador) if total_denominador else 0.0
    acuracia_categoria = (
        acertos_categoria / categorias_esperadas if categorias_esperadas else None
    )
    if provider_compartilhado is not None:
        uso = _agregar_uso(provider_compartilhado)
    elif usar_fake:
        uso = Uso(disponivel=False, motivo="execução com provedor fake (offline)")
    else:
        uso = Uso(disponivel=False, motivo=f"provedor '{provider_nome}' indisponível")

    return Relatorio(
        provider=("fake" if usar_fake else provider_nome),
        set_versao=manifesto.versao,
        threshold=str(limite),
        gerado_em=datetime.now(UTC).isoformat(),
        total_casos=len(manifesto.casos),
        total_itens_esperados=total_esperados,
        total_acertos=total_acertos,
        total_denominador=total_denominador,
        acuracia_por_item=round(acuracia, 4),
        acuracia_categoria=(
            round(acuracia_categoria, 4) if acuracia_categoria is not None else None
        ),
        latencia_p50_ms=round(_percentil(latencias, 50), 2),
        latencia_p95_ms=round(_percentil(latencias, 95), 2),
        uso=uso,
        aprovado=(total_denominador > 0 and Decimal(str(acuracia)) >= limite),
        observacoes=observacoes,
        casos=casos_resultado,
    )


def _caso_com_erro(caso: CasoAvaliacao, erro: str) -> CasoResultado:
    return CasoResultado(
        id=caso.id,
        tipo=caso.tipo.value,
        itens_esperados=len(caso.itens_esperados),
        itens_computados=0,
        acertos=0,
        denominador=len(caso.itens_esperados),
        latencia_ms=0.0,
        erro=erro,
    )
