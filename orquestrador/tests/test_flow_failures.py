"""Extraction failure vs provider failure in the flow.

Unreadable input and provider failure are handled
apart:
  - a blurry photo (empty extraction) -> ask for a clearer photo, no alert;
  - a provider timeout then success -> the flow continues, no error shown;
  - total provider unavailability -> "unavailable", nothing recorded, the
    conversation state is unchanged, the AI-error counter is fed.

Plus the guard that an empty extraction never touches the AI-error counter.
All on the fake channel, core stub and a fake LLM, with an injected no-op sleep
so the bounded retry runs instantly.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import httpx
import pytest
from PIL import Image

from app.channel.fake import FakeChannel
from app.channel.port import InboundMessage, MessageKind
from app.core.client import CoreClient
from app.core.stub import CoreStub
from app.flow.handler import InboundHandler
from app.flow.intent import ReplyIntent
from app.flow.models import FlowDeps
from app.llm.port import LlmInvalidOutputError, LlmProviderError, LlmRequest, Schema
from app.llm.retry import RetrySettings
from app.purchase.extracao import ExtracaoService
from app.purchase.rendering import Categoria
from tests.flow_fakes import FakeState

pytestmark = pytest.mark.asyncio

BENEFICIARIA = "5511999999999"
CATEGORIAS = [Categoria(idCategoriaInsumo=1, nome="Básicos")]

_CATALOGO = {
    "itens": [{"idInsumo": 1, "nome": "Arroz", "unidade": "KG"}],
    "total": 1,
    "proximoCursor": None,
}

_ARROZ = (
    '{"itens":[{"acao":"ATUALIZAR","insumo_id":1,"nome_extraido":"Arroz",'
    '"quantidade_embalagens":2,"tamanho_embalagem":5,"unidade_embalagem":"KG",'
    '"unidade":"KG","termos_busca":[]}],"chave_acesso":null}'
)
_EMPTY = '{"itens":[],"chave_acesso":null}'


class ProgrammableLlm:
    """Fake LlmPort driven by a list of actions: raise an exception or return a
    recorded JSON string. Each `generate` consumes the next action."""

    def __init__(self, actions: list) -> None:
        self._actions = list(actions)
        self.calls = 0

    def queue(self, action) -> None:
        self._actions.append(action)

    async def generate(self, request: LlmRequest, schema: type[Schema]) -> Schema:
        self.calls += 1
        if not self._actions:
            raise AssertionError("no more scripted LLM actions")
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return schema.model_validate_json(action)


class MonitorSpy:
    """Captures the AI-health calls the handler makes (health counters)."""

    def __init__(self) -> None:
        self.errors = 0
        self.successes = 0

    async def record_ai_error(self) -> None:
        self.errors += 1

    async def record_ai_success(self) -> None:
        self.successes += 1


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class Harness:
    def __init__(self, http: httpx.AsyncClient, llm: ProgrammableLlm) -> None:
        self.channel = FakeChannel()
        self.state = FakeState()
        self.monitor = MonitorSpy()
        self.llm = llm
        core = CoreClient(http)
        deps = FlowDeps(
            state=self.state,
            channel=self.channel,
            core=core,
            llm=llm,
            classifier=_NeverClassifier(),
            extracao=ExtracaoService(llm, core),
            beneficiaria=BENEFICIARIA,
            categorias_provider=self._categorias,
            max_ajustes=2,
            llm_retry=RetrySettings(max_tentativas=3, backoff_base_s=0.0),
            llm_retry_sleep=self._no_sleep,
        )
        self.handler = InboundHandler(deps, monitor=self.monitor)

    async def _categorias(self) -> list[Categoria]:
        return CATEGORIAS

    async def _no_sleep(self, seconds: float) -> None:
        return None

    @property
    def last(self) -> str:
        return self.channel.sent_texts[-1].text

    @property
    def texts(self) -> list[str]:
        return [t.text for t in self.channel.sent_texts]

    async def send_image(self, mid: str) -> None:
        self.channel.stage_media("ref-1", _png_bytes())
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.IMAGE,
                timestamp=datetime.now(tz=timezone.utc),
                media_ref="ref-1",
            )
        )

    async def send_text(self, text: str, mid: str) -> None:
        await self.handler.handle(
            InboundMessage(
                id=mid,
                sender=BENEFICIARIA,
                kind=MessageKind.TEXT,
                timestamp=datetime.now(tz=timezone.utc),
                text=text,
            )
        )


class _NeverClassifier:
    async def classify(self, text: str):  # pragma: no cover - not reached here
        raise AssertionError("classify must not be called")


def _stub() -> CoreStub:
    stub = CoreStub()
    stub.json("GET", "/insumos/catalogo", 200, _CATALOGO)
    return stub


# --- Scenario: unreadable receipt (blurry photo) ---------------------------


async def test_blurry_photo_asks_for_clearer_input_no_alert() -> None:
    llm = ProgrammableLlm([_EMPTY])  # the AI read no items from the photo
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_image("m1")

    assert "foto mais nítida" in h.last
    # Extraction failure raises no operational alert.
    assert h.monitor.errors == 0
    assert h.monitor.successes == 0
    # State stays clean: no draft was written.
    assert await h.state.load_conversation(BENEFICIARIA) == {}


# --- Scenario: provider timeout then success -------------------------------


async def test_provider_timeout_then_success_continues() -> None:
    llm = ProgrammableLlm(
        [LlmProviderError("timeout", transient=True), _ARROZ]
    )
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_image("m1")

    # The retry recovered: the owner sees the reading ack and the confirmation,
    # never an error, and a usable reading resets the AI-error streak.
    assert any("Recebi" in t for t in h.texts)
    assert "Entendi esta compra" in h.last
    assert h.monitor.successes == 1
    assert h.monitor.errors == 0
    assert llm.calls == 2  # first call timed out, retry succeeded


# --- Scenario: provider unavailable ----------------------------------------


async def test_provider_unavailable_reports_and_counts_error() -> None:
    # Every attempt fails with a transient provider error: the bounded retry is
    # exhausted and the provider failure surfaces.
    llm = ProgrammableLlm([LlmProviderError("down", transient=True)] * 3)
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_image("m1")

    assert "não consegui processar agora" in h.last.lower()
    assert "não registrei nada" in h.last.lower()
    # The failure counts toward the AI-error alert and the state is unchanged.
    assert h.monitor.errors == 1
    assert h.monitor.successes == 0
    assert await h.state.load_conversation(BENEFICIARIA) == {}
    assert llm.calls == 3  # the attempt limit


async def test_non_transient_provider_error_reports_without_retry() -> None:
    llm = ProgrammableLlm([LlmProviderError("auth", transient=False)])
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_image("m1")

    assert "não registrei nada" in h.last.lower()
    assert h.monitor.errors == 1
    assert llm.calls == 1  # not retried


# --- Invalid output is an extraction failure, not an outage ----------------


async def test_invalid_output_is_extraction_failure_no_alert() -> None:
    llm = ProgrammableLlm([LlmInvalidOutputError("schema violation")])
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_image("m1")

    assert "foto mais nítida" in h.last
    assert h.monitor.errors == 0


# --- Empty extraction never touches the AI-error counter -------------------


async def test_empty_text_extraction_does_not_touch_ai_counter() -> None:
    llm = ProgrammableLlm([_EMPTY])
    stub = _stub()
    async with stub.client() as http:
        h = Harness(http, llm)
        await h.send_text("ok", "m1")

    # A loose text reply with no items is "nothing pending", never an alert.
    assert "Não tem nada pendente" in h.last
    assert h.monitor.errors == 0
    assert h.monitor.successes == 0
