"""The service token travels on every core call and never reaches the logs."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from app.channel.fake import FakeChannel
from app.config import Settings
from app.core.client import HEADER_TOKEN, CoreClient, criar_http_core
from app.core.errors import CoreAuthError
from app.core.models import (
    ConfirmacaoEnvioRequest,
    MovimentoRequest,
    NotificacaoRequest,
    ObterOuCriarRequest,
    OrigemMovimentacao,
    StatusEnvioNotificacao,
    TipoNotificacao,
    UnidadeMedida,
)
from app.core.stub import CoreStub
from app.delivery.settings import DeliverySettings
from app.delivery.state import DeliveryState
from app.delivery.worker import DeliveryWorker
from app.flow.models import Draft, DraftItem
from app.llm.schemas import AcaoItem
from app.logging import JsonFormatter
from app.recording import Recorder
from tests.fake_redis import FakeRedis
from tests.flow_fakes import FakeState

TOKEN = "token-ficticio-de-teste-0000000000000000"
RECIPIENT = "5511900000001"
CHAVE = "3" * 44
AGORA = datetime(2026, 6, 15, 8, 5, tzinfo=timezone.utc)

MOVIMENTO_OK = {
    "idMovimentacao": 7,
    "insumoId": 1,
    "tipo": "ENTRADA_COMPRA",
    "quantidade": "10.000",
    "saldoAtual": "10.000",
    "duplicado": False,
}


def _movimento() -> MovimentoRequest:
    return MovimentoRequest(
        insumoId=1,
        quantidadeInformada=Decimal("10.000"),
        unidadeInformada=UnidadeMedida.KG,
        origem=OrigemMovimentacao.TEXTO,
    )


Call = Callable[[CoreClient], Awaitable[object]]

# One row per CoreClient method: (method, path, ok status, ok body, call).
ROTAS: dict[str, tuple[str, str, int, object, Call]] = {
    "listar_catalogo": (
        "GET",
        "/insumos/catalogo",
        200,
        {"itens": [], "total": 0},
        lambda core: core.listar_catalogo(q="chocolate"),
    ),
    "obter_ou_criar_insumo": (
        "POST",
        "/insumos/obter-ou-criar",
        201,
        {"idInsumo": 9, "nome": "Farinha", "unidade": "KG"},
        lambda core: core.obter_ou_criar_insumo(
            ObterOuCriarRequest(nome="Farinha", unidade=UnidadeMedida.KG)
        ),
    ),
    "consultar_nota": (
        "GET",
        f"/notas-fiscais/{CHAVE}/status",
        200,
        {"status": "INEXISTENTE", "insumoIds": []},
        lambda core: core.consultar_nota(CHAVE),
    ),
    "registrar_movimento": (
        "POST",
        "/estoque/movimentos",
        201,
        MOVIMENTO_OK,
        lambda core: core.registrar_movimento("fluxo:1:1", _movimento()),
    ),
    "listar_agenda": (
        "GET",
        "/notificacoes/agenda",
        200,
        [],
        lambda core: core.listar_agenda(AGORA, AGORA),
    ),
    "confirmar_envio": (
        "POST",
        "/notificacoes/5/confirmacao-envio",
        200,
        {"idNotificacao": 5},
        lambda core: core.confirmar_envio(
            5, ConfirmacaoEnvioRequest(statusEnvio=StatusEnvioNotificacao.ENVIADA)
        ),
    ),
    "listar_categorias": (
        "GET",
        "/categorias-insumo",
        200,
        [],
        lambda core: core.listar_categorias(),
    ),
    "criar_notificacao": (
        "POST",
        "/notificacoes",
        201,
        {"idNotificacao": 1},
        lambda core: core.criar_notificacao(
            NotificacaoRequest(
                tipo=TipoNotificacao.ALERTA_OPERACIONAL, titulo="Título", mensagem="Mensagem"
            )
        ),
    ),
}


def _http(stub: CoreStub) -> httpx.AsyncClient:
    return criar_http_core(stub.base_url, TOKEN, transport=stub.transport())


@pytest.mark.parametrize("nome", ROTAS)
async def test_todas_as_chamadas_levam_o_token(nome: str) -> None:
    metodo, caminho, status, corpo, chamar = ROTAS[nome]
    stub = CoreStub()
    stub.json(metodo, caminho, status, corpo)
    async with _http(stub) as http:
        await chamar(CoreClient(http))
    assert [request.headers[HEADER_TOKEN] for request in stub.requests] == [TOKEN]


async def test_token_convive_com_idempotency_key() -> None:
    stub = CoreStub()
    stub.json("POST", "/estoque/movimentos", 201, MOVIMENTO_OK)
    async with _http(stub) as http:
        await CoreClient(http).registrar_movimento("fluxo:1:1", _movimento())
    sent = stub.requests[0]
    assert sent.headers[HEADER_TOKEN] == TOKEN
    assert sent.headers["Idempotency-Key"] == "fluxo:1:1"


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("nome", ROTAS)
async def test_401_403_viram_core_auth_error(
    nome: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    metodo, caminho, _ok, _corpo, chamar = ROTAS[nome]
    stub = CoreStub()
    stub.status(metodo, caminho, status)
    caplog.set_level(logging.WARNING, logger="core.client")
    async with _http(stub) as http:
        with pytest.raises(CoreAuthError) as exc:
            await chamar(CoreClient(http))
    assert exc.value.status == status
    assert exc.value.transient is False
    registros = [r for r in caplog.records if r.getMessage() == "core_auth_rejected"]
    assert len(registros) == 1
    assert registros[0].levelno == logging.WARNING
    assert registros[0].status == status
    assert registros[0].method == metodo
    assert TOKEN not in caplog.text


class _RelogioFixo:
    def now(self) -> datetime:
        return AGORA


class _SorteioNoInicio:
    def uniform(self, low: float, _high: float) -> float:
        return low


async def test_token_nunca_aparece_em_logs(
    base_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings.from_env()
    assert settings.token_core == TOKEN
    assert TOKEN not in repr(settings)

    stub = CoreStub()
    stub.json("POST", "/estoque/movimentos", 201, MOVIMENTO_OK)
    stub.json(
        "GET",
        "/notificacoes/agenda",
        200,
        [
            {
                "idNotificacao": 1,
                "janelaInicio": (AGORA - timedelta(minutes=5)).isoformat(),
                "janelaFim": (AGORA + timedelta(minutes=5)).isoformat(),
                "templateKey": "resumo_inicio_dia",
                "parametros": "{}",
            }
        ],
    )
    stub.json("POST", "/notificacoes/1/confirmacao-envio", 200, {"idNotificacao": 1})
    stub.status("GET", "/categorias-insumo", 403)

    caplog.handler.setFormatter(JsonFormatter())
    caplog.set_level(logging.DEBUG)

    draft = Draft(
        fluxo_id="fluxo-1",
        chave_acesso=None,
        origem=OrigemMovimentacao.TEXTO,
        itens=[
            DraftItem(
                index=0,
                acao=AcaoItem.ATUALIZAR,
                insumo_id=1,
                nome="Farinha",
                unidade=UnidadeMedida.KG,
                quantidade_total=Decimal(10),
                calculo="10 kg",
            )
        ],
    )
    channel = FakeChannel()
    async with criar_http_core(
        stub.base_url, settings.token_core, transport=stub.transport()
    ) as http:
        core = CoreClient(http)
        resultado = await Recorder(FakeState(), core).record(draft)
        await DeliveryWorker(
            core=core,
            channel=channel,
            state=DeliveryState(FakeRedis()),
            recipient=RECIPIENT,
            settings=DeliverySettings(
                confirm_base_s=0, confirm_cap_s=0, confirm_max_tentativas=1
            ),
            clock=_RelogioFixo(),
            random_source=_SorteioNoInicio(),
        ).poll_once()
        with pytest.raises(CoreAuthError):
            await core.listar_categorias()

    assert resultado.all_ok
    assert len(channel.sent_notifications) == 1
    assert all(request.headers[HEADER_TOKEN] == TOKEN for request in stub.requests)
    # The JSON lines are inspected as they would reach stdout.
    assert '"event": "core_auth_rejected"' in caplog.text
    assert TOKEN not in caplog.text
