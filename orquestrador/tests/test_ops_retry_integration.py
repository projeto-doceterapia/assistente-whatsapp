"""End-to-end integration over the production wiring.

A purchase runs through the real `InboundHandler`, retry worker and coordinator,
with the ops `PurchaseNotifier`, `OperationalAlerts` and `HealthMonitor` wired as
`main` wires them, over the fake channel, core stub, fake LLM and fake Redis.

It asserts the three cross-track guarantees the wiring commits add:
  - a recorded purchase creates exactly one in-app COMPRA_REGISTRADA;
  - a transient recording failure defers the final message to the worker;
  - once the automatic retries are exhausted, exactly one DLQ alert is raised.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.channel.fake import FakeChannel
from app.channel.port import InboundMessage, MessageKind
from app.core.client import CoreClient
from app.core.models import TipoNotificacao
from app.core.stub import CoreStub
from app.flow.handler import InboundHandler
from app.flow.intent import ReplyIntent
from app.flow.models import FlowDeps
from app.llm.fake import FakeLlmProvider
from app.ops import HealthMonitor, OperationalAlerts, OpsSettings, PurchaseNotifier
from app.purchase.extracao import ExtracaoService
from app.purchase.rendering import Categoria
from app.recording import Recorder
from app.retry import create_retry_coordinator, create_retry_worker
from tests.fake_redis import FakeRedis
from tests.flow_fakes import FakeState
from tests.test_retry_on_request import (
    BENEFICIARIA,
    CATEGORIAS,
    FakeClassifier,
    _CATALOGO,
    _extracao_json,
    _item_json,
    _ok,
)
from tests.test_retry_worker import FakeClock, FixedRandom

pytestmark = pytest.mark.asyncio


def _notificacoes(stub: CoreStub, tipo: TipoNotificacao) -> list[dict]:
    """The notification bodies of the given type the orchestrator posted."""
    bodies = []
    for request in stub.requests:
        if request.method == "POST" and request.url.path == "/notificacoes":
            body = json.loads(request.content)
            if body.get("tipo") == tipo.value:
                bodies.append(body)
    return bodies


class FullWiring:
    """The production object graph over fakes, mirroring `main._build_wiring`."""

    def __init__(self, http: httpx.AsyncClient, llm: FakeLlmProvider, clock: FakeClock) -> None:
        self.channel = FakeChannel()
        self.state = FakeState()
        self.classifier = FakeClassifier()
        self.redis = FakeRedis()
        self.clock = clock
        core = CoreClient(http)
        ops_settings = OpsSettings(ai_failure_threshold=3, core_failure_threshold=3)
        self.alerts = OperationalAlerts(
            core=core,
            channel=self.channel,
            redis=self.redis,
            recipient=BENEFICIARIA,
            settings=ops_settings,
        )
        self.coordinator = create_retry_coordinator(
            redis=self.redis,
            recipient=BENEFICIARIA,
            clock=clock,
            random_source=FixedRandom(1.0),
        )
        self.purchases = PurchaseNotifier(core)
        self.monitor = HealthMonitor(
            alerts=self.alerts,
            channel=self.channel,
            core=core,
            redis=self.redis,
            settings=ops_settings,
        )
        self.worker = create_retry_worker(
            redis=self.redis,
            conversation=self.state,
            channel=self.channel,
            replay=Recorder(self.state, core).record,
            coordinator=self.coordinator,
            clock=clock,
            random_source=FixedRandom(1.0),
            alerts=self.alerts,
        )
        deps = FlowDeps(
            state=self.state,
            channel=self.channel,
            core=core,
            llm=llm,
            classifier=self.classifier,
            extracao=ExtracaoService(llm, core),
            beneficiaria=BENEFICIARIA,
            categorias_provider=self._categorias,
            max_ajustes=2,
        )
        self.handler = InboundHandler(deps, self.coordinator, self.purchases, self.monitor)

    async def _categorias(self) -> list[Categoria]:
        return CATEGORIAS

    @property
    def texts(self) -> list[str]:
        return [t.text for t in self.channel.sent_texts]

    async def text(self, text: str, mid: str) -> None:
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.TEXT,
                timestamp=datetime.now(tz=timezone.utc),
                text=text,
            )
        )

    async def drain(self, steps: int = 6) -> None:
        for _ in range(steps):
            self.clock.advance(300)
            await self.worker.poll_once()


def _stub(movimento_handler) -> CoreStub:
    stub = CoreStub()
    stub.json("GET", "/insumos/catalogo", 200, _CATALOGO)
    stub.on("POST", "/estoque/movimentos", movimento_handler)
    stub.status("POST", "/notificacoes", 201)
    return stub


async def test_success_creates_one_compra_registrada() -> None:
    llm = FakeLlmProvider([_extracao_json([_item_json(nome="Arroz")])])
    clock = FakeClock()
    stub = _stub(_ok)
    async with stub.client() as http:
        w = FullWiring(http, llm, clock)
        await w.text("comprei 2 x 5 kg de arroz", "m1")
        w.classifier.queue(ReplyIntent.CONFIRM)
        await w.text("sim", "m2")

    # The purchase recorded on the first try: exactly one COMPRA_REGISTRADA, and
    # the owner got the success message immediately (no deferral needed).
    assert len(_notificacoes(stub, TipoNotificacao.COMPRA_REGISTRADA)) == 1
    assert any("Pronto! Atualizei seu estoque" in t for t in w.texts)


async def test_transient_defers_then_exhausts_with_one_dlq_alert() -> None:
    def movimento(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("core down", request=request)  # always transient

    llm = FakeLlmProvider([_extracao_json([_item_json(nome="Arroz")])])
    clock = FakeClock()
    stub = _stub(movimento)
    async with stub.client() as http:
        w = FullWiring(http, llm, clock)
        await w.text("comprei 2 x 5 kg de arroz", "m1")
        w.classifier.queue(ReplyIntent.CONFIRM)
        await w.text("sim", "m2")

        # The transient failure deferred the final message to the worker: no
        # final result has been sent yet (only reading/recording acks).
        assert all("Não consegui" not in t for t in w.texts)
        assert all("Pronto" not in t for t in w.texts)

        # Drive the retries until the attempts are exhausted and the item hits
        # the DLQ. The worker then sends exactly one final (failure) message.
        await w.drain()

    # Exactly one DLQ alert (one ALERTA_OPERACIONAL in-app notification), raised
    # once despite the item being polled several times (cool-down dedup).
    assert len(_notificacoes(stub, TipoNotificacao.ALERTA_OPERACIONAL)) == 1
    # No purchase was recorded, so no COMPRA_REGISTRADA was ever created.
    assert len(_notificacoes(stub, TipoNotificacao.COMPRA_REGISTRADA)) == 0
