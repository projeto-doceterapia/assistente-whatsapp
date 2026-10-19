from __future__ import annotations

import pytest

from app.core.client import CoreClient
from app.core.stub import CoreStub
from app.ops.categorias import CategoriasProvider
from app.purchase.rendering import CATEGORIA_PADRAO

CATEGORIAS_PATH = "/categorias-insumo"


class FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


async def test_fetches_and_maps_categorias() -> None:
    stub = CoreStub()
    stub.json(
        "GET",
        CATEGORIAS_PATH,
        200,
        [{"idCategoriaInsumo": 1, "nome": "Farináceos", "descricao": None}],
    )
    async with stub.client() as http:
        provider = CategoriasProvider(CoreClient(http), ttl_s=300)
        categorias = await provider()
    assert categorias[0].idCategoriaInsumo == 1
    assert categorias[0].nome == "Farináceos"


async def test_caches_within_ttl() -> None:
    stub = CoreStub()
    stub.json("GET", CATEGORIAS_PATH, 200, [{"idCategoriaInsumo": 1, "nome": "A"}])
    clock = FakeClock()
    async with stub.client() as http:
        provider = CategoriasProvider(CoreClient(http), ttl_s=300, monotonic=clock)
        await provider()
        await provider()
    # Second call within TTL served from cache: only one core request.
    assert len([r for r in stub.requests if r.url.path == CATEGORIAS_PATH]) == 1


async def test_refetches_after_ttl() -> None:
    stub = CoreStub()
    stub.json("GET", CATEGORIAS_PATH, 200, [{"idCategoriaInsumo": 1, "nome": "A"}])
    clock = FakeClock()
    async with stub.client() as http:
        provider = CategoriasProvider(CoreClient(http), ttl_s=300, monotonic=clock)
        await provider()
        clock.advance(301)
        await provider()
    assert len([r for r in stub.requests if r.url.path == CATEGORIAS_PATH]) == 2


async def test_fallback_to_default_when_core_down_and_no_cache() -> None:
    stub = CoreStub()
    stub.status("GET", CATEGORIAS_PATH, 503)
    async with stub.client() as http:
        provider = CategoriasProvider(CoreClient(http), ttl_s=300)
        categorias = await provider()
    assert len(categorias) == 1
    assert categorias[0].nome == CATEGORIA_PADRAO


async def test_serves_stale_cache_when_core_goes_down() -> None:
    stub = CoreStub()
    stub.json("GET", CATEGORIAS_PATH, 200, [{"idCategoriaInsumo": 2, "nome": "Doces"}])
    clock = FakeClock()
    async with stub.client() as http:
        provider = CategoriasProvider(CoreClient(http), ttl_s=10, monotonic=clock)
        await provider()
        stub.status("GET", CATEGORIAS_PATH, 503)  # core now down
        clock.advance(11)  # cache expired
        categorias = await provider()
    # Stale cache is preferred over the default when the core is unreachable.
    assert categorias[0].nome == "Doces"
