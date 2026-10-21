from __future__ import annotations

import httpx

from app.core.client import CoreClient
from app.core.models import CatalogoItem, CatalogoPagina, UnidadeMedida
from app.core.stub import CoreStub
from app.llm.fake import FakeLlmProvider
from app.llm.schemas import AcaoItem
from app.purchase.extracao import ExtracaoConfig, ExtracaoService
from app.purchase.rendering import Categoria, render_catalogo, render_contexto

CATEGORIAS = [Categoria(idCategoriaInsumo=1, nome="Básicos")]


def _catalogo(n: int) -> list[CatalogoItem]:
    unidades = list(UnidadeMedida)
    return [
        CatalogoItem(
            idInsumo=i,
            nome=f"Insumo de teste número {i}",
            unidade=unidades[i % len(unidades)],
        )
        for i in range(1, n + 1)
    ]


def test_render_catalogo_is_compact_pipe_lines() -> None:
    linhas = render_catalogo(
        [CatalogoItem(idInsumo=7, nome="Açúcar", unidade=UnidadeMedida.G)]
    )
    assert linhas == "7|Açúcar|G"


def test_render_contexto_200_items_stays_compact() -> None:
    contexto = render_contexto(_catalogo(200), CATEGORIAS, total=200, parcial=False)
    chars = len(contexto)
    # Rough token estimate: ~4 chars per token. Budget is 2-3 k tokens.
    tokens_estimados = chars / 4
    assert tokens_estimados < 3000, tokens_estimados
    assert "CATÁLOGO COMPLETO: 200 insumos." in contexto
    assert "KG <-> G" in contexto


def test_render_contexto_partial_announces_total() -> None:
    contexto = render_contexto(_catalogo(200), CATEGORIAS, total=450, parcial=True)
    assert "CATÁLOGO PARCIAL: mostrando 200 de 450 insumos." in contexto


def _extracao_json(items: str) -> str:
    return f'{{"itens":[{items}],"chave_acesso":null}}'


def _match(insumo_id: int) -> str:
    return (
        f'{{"acao":"ATUALIZAR","insumo_id":{insumo_id},"nome_extraido":"x",'
        '"quantidade_embalagens":1,"tamanho_embalagem":1,"unidade_embalagem":"KG",'
        '"termos_busca":[]}'
    )


def _clarify_with_search(termo: str) -> str:
    return (
        '{"acao":"ESCLARECER","nome_extraido":"Leite condensado",'
        '"quantidade_embalagens":1,"tamanho_embalagem":1,"unidade_embalagem":"UNIDADE",'
        f'"termos_busca":["{termo}"]}}'
    )


async def test_small_catalog_single_ai_call() -> None:
    pagina = CatalogoPagina(itens=_catalogo(3), total=3, proximoCursor=None)
    llm = FakeLlmProvider([_extracao_json(_match(1))])
    stub = CoreStub()
    async with stub.client() as http:
        service = ExtracaoService(llm, CoreClient(http))
        resultado = await service.extrair(pagina, CATEGORIAS, text="comprei insumo 1")
    assert len(llm.requests) == 1
    assert resultado.itens[0].acao is AcaoItem.ATUALIZAR
    assert stub.requests == []  # no catalog search ran


async def test_item_found_through_search() -> None:
    pagina = CatalogoPagina(itens=_catalogo(200), total=450, proximoCursor="cursor-1")
    first = _extracao_json(_clarify_with_search("leite condensado"))
    second = _extracao_json(_match(301))
    llm = FakeLlmProvider([first, second])

    stub = CoreStub()
    found = {
        "itens": [{"idInsumo": 301, "nome": "Leite condensado", "unidade": "UNIDADE"}],
        "total": 1,
        "proximoCursor": None,
    }
    stub.json("GET", "/insumos/catalogo", 200, found)

    async with stub.client() as http:
        service = ExtracaoService(llm, CoreClient(http))
        resultado = await service.extrair(pagina, CATEGORIAS, text="comprei leite")

    assert len(llm.requests) == 2  # initial call + re-match on candidates
    assert len(stub.requests) == 1  # one ?q= search
    assert stub.requests[0].url.params.get("q") == "leite condensado"
    assert resultado.itens[0].acao is AcaoItem.ATUALIZAR
    assert resultado.itens[0].insumo_id == 301


async def test_rounds_exhausted_leaves_item_unmatched() -> None:
    pagina = CatalogoPagina(itens=_catalogo(200), total=450, proximoCursor="cursor-1")
    # The AI never matches; it keeps returning a clarify with search terms.
    still_unmatched = _extracao_json(_clarify_with_search("inexistente"))
    llm = FakeLlmProvider([still_unmatched, still_unmatched])

    stub = CoreStub()
    stub.json("GET", "/insumos/catalogo", 200, {"itens": [], "total": 0, "proximoCursor": None})

    async with stub.client() as http:
        service = ExtracaoService(llm, CoreClient(http), ExtracaoConfig(rodadas_busca=1))
        resultado = await service.extrair(pagina, CATEGORIAS, text="algo")

    # One search round ran; the item stays clarify, never matched to an invented id.
    assert resultado.itens[0].acao is AcaoItem.ESCLARECER
    assert resultado.itens[0].insumo_id is None
