from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.client import CoreClient
from app.core.errors import (
    CoreConflictError,
    CoreNotFoundError,
    CoreServerError,
    CoreTimeoutError,
    CoreValidationError,
)
from app.core.models import (
    ConfirmacaoEnvioRequest,
    MovimentoRequest,
    NotificacaoRequest,
    ObterOuCriarRequest,
    OrigemMovimentacao,
    StatusEnvioNotificacao,
    StatusNotaFiscal,
    TipoNotificacao,
    UnidadeMedida,
)
from app.core.stub import CoreStub

MOVIMENTO_PATH = "/estoque/movimentos"


def _movimento() -> MovimentoRequest:
    return MovimentoRequest(
        insumoId=1,
        quantidadeInformada=Decimal("10.000"),
        unidadeInformada=UnidadeMedida.KG,
        origem=OrigemMovimentacao.NOTA_IMAGEM,
        valorLiquido=Decimal("15.00"),
    )


def _movimento_body(duplicado: bool) -> dict[str, object]:
    return {
        "idMovimentacao": 7,
        "insumoId": 1,
        "tipo": "ENTRADA_COMPRA",
        "quantidade": "10.000",
        "saldoAtual": "10.000",
        "duplicado": duplicado,
        "custoUnitario": "1.5000",
    }


async def test_movimento_created_201() -> None:
    stub = CoreStub()
    stub.json("POST", MOVIMENTO_PATH, 201, _movimento_body(False))
    async with stub.client() as http:
        result = await CoreClient(http).registrar_movimento("fluxo:1:1", _movimento())
    assert result.duplicado is False
    assert result.idMovimentacao == 7
    sent = stub.requests[0]
    assert sent.headers["Idempotency-Key"] == "fluxo:1:1"


async def test_movimento_duplicate_200() -> None:
    stub = CoreStub()
    stub.json("POST", MOVIMENTO_PATH, 200, _movimento_body(True))
    async with stub.client() as http:
        result = await CoreClient(http).registrar_movimento("fluxo:1:1", _movimento())
    assert result.duplicado is True


async def test_movimento_conflict_409() -> None:
    stub = CoreStub()
    stub.status("POST", MOVIMENTO_PATH, 409)
    async with stub.client() as http:
        with pytest.raises(CoreConflictError) as exc:
            await CoreClient(http).registrar_movimento("fluxo:1:1", _movimento())
    assert exc.value.transient is False


async def test_movimento_validation_422() -> None:
    stub = CoreStub()
    stub.status("POST", MOVIMENTO_PATH, 422)
    async with stub.client() as http:
        with pytest.raises(CoreValidationError) as exc:
            await CoreClient(http).registrar_movimento("fluxo:1:1", _movimento())
    assert exc.value.transient is False


async def test_movimento_not_found_404() -> None:
    stub = CoreStub()
    stub.status("POST", MOVIMENTO_PATH, 404)
    async with stub.client() as http:
        with pytest.raises(CoreNotFoundError) as exc:
            await CoreClient(http).registrar_movimento("fluxo:1:1", _movimento())
    assert exc.value.transient is False


async def test_movimento_server_error_5xx_is_transient() -> None:
    stub = CoreStub()
    stub.status("POST", MOVIMENTO_PATH, 503)
    async with stub.client() as http:
        with pytest.raises(CoreServerError) as exc:
            await CoreClient(http).registrar_movimento("fluxo:1:1", _movimento())
    assert exc.value.transient is True


async def test_movimento_timeout_is_transient() -> None:
    stub = CoreStub()
    stub.timeout("POST", MOVIMENTO_PATH)
    async with stub.client() as http:
        with pytest.raises(CoreTimeoutError) as exc:
            await CoreClient(http).registrar_movimento("fluxo:1:1", _movimento())
    assert exc.value.transient is True


async def test_listar_catalogo_parses_page() -> None:
    stub = CoreStub()
    stub.json(
        "GET",
        "/insumos/catalogo",
        200,
        {
            "itens": [{"idInsumo": 1, "nome": "Acucar", "unidade": "KG"}],
            "total": 1,
            "proximoCursor": None,
        },
    )
    async with stub.client() as http:
        page = await CoreClient(http).listar_catalogo(q="acucar")
    assert page.total == 1
    assert page.itens[0].idInsumo == 1
    assert stub.requests[0].url.params["q"] == "acucar"


async def test_obter_ou_criar_marks_created_on_201() -> None:
    stub = CoreStub()
    stub.json("POST", "/insumos/obter-ou-criar", 201, {"idInsumo": 9, "nome": "Farinha"})
    async with stub.client() as http:
        result = await CoreClient(http).obter_ou_criar_insumo(
            ObterOuCriarRequest(nome="Farinha", unidade=UnidadeMedida.KG)
        )
    assert result.criado is True
    assert result.insumo.idInsumo == 9


async def test_obter_ou_criar_long_name_422() -> None:
    stub = CoreStub()
    stub.status("POST", "/insumos/obter-ou-criar", 422)
    async with stub.client() as http:
        with pytest.raises(CoreValidationError):
            await CoreClient(http).obter_ou_criar_insumo(
                ObterOuCriarRequest(nome="x" * 46, unidade=UnidadeMedida.KG)
            )


async def test_consultar_nota_registrada() -> None:
    stub = CoreStub()
    chave = "3" * 44
    stub.json(
        "GET",
        f"/notas-fiscais/{chave}/status",
        200,
        {"status": "REGISTRADA", "insumoIds": [1, 2]},
    )
    async with stub.client() as http:
        status = await CoreClient(http).consultar_nota(chave)
    assert status.status is StatusNotaFiscal.REGISTRADA
    assert status.insumoIds == [1, 2]


async def test_listar_agenda_parses_items() -> None:
    stub = CoreStub()
    stub.json(
        "GET",
        "/notificacoes/agenda",
        200,
        [
            {
                "idNotificacao": 1,
                "janelaInicio": "2026-06-14T08:00:00",
                "janelaFim": "2026-06-14T08:10:00",
                "templateKey": "resumo_inicio_dia",
                "parametros": "{}",
            }
        ],
    )
    de = datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc)
    ate = datetime(2026, 6, 14, 23, 59, tzinfo=timezone.utc)
    async with stub.client() as http:
        agenda = await CoreClient(http).listar_agenda(de, ate)
    assert agenda[0].templateKey == "resumo_inicio_dia"


async def test_confirmar_envio_posts_status() -> None:
    stub = CoreStub()
    stub.json("POST", "/notificacoes/5/confirmacao-envio", 200, {"idNotificacao": 5})
    async with stub.client() as http:
        await CoreClient(http).confirmar_envio(
            5, ConfirmacaoEnvioRequest(statusEnvio=StatusEnvioNotificacao.ENVIADA, canalMessageId="m1")
        )
    assert stub.requests[0].url.path == "/notificacoes/5/confirmacao-envio"


async def test_listar_categorias_parses_items() -> None:
    stub = CoreStub()
    stub.json(
        "GET",
        "/categorias-insumo",
        200,
        [
            {"idCategoriaInsumo": 1, "nome": "Farináceos", "descricao": "Farinhas"},
            {"idCategoriaInsumo": 2, "nome": "Laticínios", "descricao": None},
        ],
    )
    async with stub.client() as http:
        categorias = await CoreClient(http).listar_categorias()
    assert [c.idCategoriaInsumo for c in categorias] == [1, 2]
    assert categorias[0].nome == "Farináceos"
    assert categorias[1].descricao is None


async def test_listar_categorias_server_error_is_transient() -> None:
    stub = CoreStub()
    stub.status("GET", "/categorias-insumo", 503)
    async with stub.client() as http:
        with pytest.raises(CoreServerError) as exc:
            await CoreClient(http).listar_categorias()
    assert exc.value.transient is True


async def test_listar_categorias_timeout_is_transient() -> None:
    stub = CoreStub()
    stub.timeout("GET", "/categorias-insumo")
    async with stub.client() as http:
        with pytest.raises(CoreTimeoutError) as exc:
            await CoreClient(http).listar_categorias()
    assert exc.value.transient is True


async def test_criar_notificacao_posts_body() -> None:
    stub = CoreStub()
    stub.json("POST", "/notificacoes", 201, {"idNotificacao": 42})
    async with stub.client() as http:
        await CoreClient(http).criar_notificacao(
            NotificacaoRequest(
                tipo=TipoNotificacao.COMPRA_REGISTRADA,
                titulo="Compra registrada",
                mensagem="2 itens registrados.",
            )
        )
    sent = stub.requests[0]
    assert sent.url.path == "/notificacoes"
    body = json.loads(sent.content)
    assert body["tipo"] == "COMPRA_REGISTRADA"
    assert body["titulo"] == "Compra registrada"
    # Optional fields are omitted when unset (exclude_none).
    assert "fkPedido" not in body
    assert "fkInsumo" not in body


async def test_criar_notificacao_validation_422() -> None:
    stub = CoreStub()
    stub.status("POST", "/notificacoes", 422)
    async with stub.client() as http:
        with pytest.raises(CoreValidationError) as exc:
            await CoreClient(http).criar_notificacao(
                NotificacaoRequest(
                    tipo=TipoNotificacao.ALERTA_OPERACIONAL,
                    titulo="x" * 151,
                    mensagem="m",
                )
            )
    assert exc.value.transient is False


async def test_criar_notificacao_server_error_is_transient() -> None:
    stub = CoreStub()
    stub.status("POST", "/notificacoes", 500)
    async with stub.client() as http:
        with pytest.raises(CoreServerError) as exc:
            await CoreClient(http).criar_notificacao(
                NotificacaoRequest(
                    tipo=TipoNotificacao.ALERTA_OPERACIONAL,
                    titulo="Alerta",
                    mensagem="m",
                )
            )
    assert exc.value.transient is True
