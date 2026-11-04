from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.client import CoreClient
from app.core.stub import CoreStub
from app.core.switches import AssistantSwitches, Switches

pytestmark = pytest.mark.asyncio

PATH = "/configuracao-notificacao"
CONFIGURACAO = {
    "inicioExpediente": "08:00",
    "diasExpediente": ["MONDAY"],
    "avisosAutomaticosAtivo": False,
    "assistenteAtivo": False,
}


class FakeClock:
    def __init__(self) -> None:
        self.instante = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.instante

    def advance(self, seconds: int) -> None:
        self.instante += timedelta(seconds=seconds)


def _reads(stub: CoreStub) -> int:
    return sum(1 for request in stub.requests if request.url.path == PATH)


async def test_reads_only_the_two_switches() -> None:
    stub = CoreStub()
    stub.json("GET", PATH, 200, CONFIGURACAO)
    async with stub.client() as http:
        switches = AssistantSwitches(CoreClient(http), FakeClock())
        assert await switches.current() == Switches(False, False)


async def test_never_read_defaults_to_active() -> None:
    stub = CoreStub()
    stub.timeout("GET", PATH)
    async with stub.client() as http:
        switches = AssistantSwitches(CoreClient(http), FakeClock())
        assert await switches.current() == Switches(True, True)


async def test_core_without_the_fields_reads_as_active() -> None:
    stub = CoreStub()
    stub.json("GET", PATH, 200, {"inicioExpediente": "08:00"})
    async with stub.client() as http:
        switches = AssistantSwitches(CoreClient(http), FakeClock())
        assert await switches.current() == Switches(True, True)


async def test_cache_ttl() -> None:
    stub = CoreStub()
    stub.json("GET", PATH, 200, CONFIGURACAO)
    clock = FakeClock()
    async with stub.client() as http:
        switches = AssistantSwitches(CoreClient(http), clock)
        await switches.current()
        clock.advance(29)
        await switches.current()
        assert _reads(stub) == 1
        clock.advance(1)
        await switches.current()
        assert _reads(stub) == 2


@pytest.mark.parametrize(
    "falha",
    [
        lambda stub: stub.timeout("GET", PATH),
        lambda stub: stub.status("GET", PATH, 500),
        lambda stub: stub.status("GET", PATH, 401),
        lambda stub: stub.on("GET", PATH, lambda _r: httpx.Response(200, text="not json")),
    ],
    ids=["timeout", "server_error", "rejected", "invalid_body"],
)
async def test_keeps_last_value_when_core_down(falha) -> None:
    stub = CoreStub()
    stub.json("GET", PATH, 200, CONFIGURACAO)
    clock = FakeClock()
    async with stub.client() as http:
        switches = AssistantSwitches(CoreClient(http), clock)
        await switches.current()
        falha(stub)
        clock.advance(30)
        assert (await switches.current()).assistente_ativo is False


async def test_failed_read_waits_for_the_ttl() -> None:
    stub = CoreStub()
    stub.timeout("GET", PATH)
    clock = FakeClock()
    async with stub.client() as http:
        switches = AssistantSwitches(CoreClient(http), clock)
        await switches.current()
        await switches.current()
        assert _reads(stub) == 1
        clock.advance(30)
        await switches.current()
        assert _reads(stub) == 2


async def test_read_failure_logs_without_content(caplog) -> None:
    stub = CoreStub()
    stub.status("GET", PATH, 500)
    async with stub.client() as http:
        await AssistantSwitches(CoreClient(http), FakeClock()).current()
    registros = [r for r in caplog.records if r.message == "switches_read_failed"]
    assert len(registros) == 1
    assert registros[0].levelname == "WARNING"


@pytest.mark.parametrize(
    ("avisos", "assistente", "esperado"),
    [(True, True, True), (False, True, False), (True, False, False), (False, False, False)],
)
async def test_notices_need_both_switches(avisos: bool, assistente: bool, esperado: bool) -> None:
    assert Switches(avisos, assistente).envia_avisos is esperado
