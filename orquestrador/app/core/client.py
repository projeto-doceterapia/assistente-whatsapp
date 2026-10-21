from __future__ import annotations

from datetime import datetime

import httpx

from app.core.errors import (
    CoreClientError,
    CoreConflictError,
    CoreNotFoundError,
    CoreServerError,
    CoreTimeoutError,
    CoreValidationError,
)
from app.core.models import (
    AgendaItem,
    CatalogoPagina,
    CategoriaInsumo,
    ConfirmacaoEnvioRequest,
    MovimentoRequest,
    MovimentoResultado,
    NotaFiscalStatus,
    NotificacaoRequest,
    ObterOuCriarRequest,
    ObterOuCriarResultado,
)


class CoreClient:
    """Async client for the core REST API.

    It is the only orchestrator component that talks to the core. Status codes
    are mapped to modeled outcomes (201 created / 200 duplicate) or to typed
    errors classified as transient (timeout, 5xx) or definitive (409/422/404).
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise CoreTimeoutError() from exc
        except httpx.TransportError as exc:
            raise CoreServerError(0) from exc
        return response

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 404:
            raise CoreNotFoundError()
        if status == 409:
            raise CoreConflictError()
        if status == 422:
            raise CoreValidationError()
        if status >= 500:
            raise CoreServerError(status)
        raise CoreClientError(status)

    async def listar_catalogo(
        self,
        q: str | None = None,
        categoria: int | None = None,
        limite: int | None = None,
        cursor: str | None = None,
    ) -> CatalogoPagina:
        params: dict[str, object] = {}
        if q is not None:
            params["q"] = q
        if categoria is not None:
            params["categoria"] = categoria
        if limite is not None:
            params["limite"] = limite
        if cursor is not None:
            params["cursor"] = cursor
        response = await self._request("GET", "/insumos/catalogo", params=params)
        self._raise_for_error(response)
        return CatalogoPagina.model_validate(response.json())

    async def obter_ou_criar_insumo(self, request: ObterOuCriarRequest) -> ObterOuCriarResultado:
        response = await self._request(
            "POST", "/insumos/obter-ou-criar", json=request.model_dump(exclude_none=True)
        )
        self._raise_for_error(response)
        criado = response.status_code == 201
        return ObterOuCriarResultado(insumo=response.json(), criado=criado)

    async def consultar_nota(self, chave: str) -> NotaFiscalStatus:
        response = await self._request("GET", f"/notas-fiscais/{chave}/status")
        self._raise_for_error(response)
        return NotaFiscalStatus.model_validate(response.json())

    async def registrar_movimento(
        self, idempotency_key: str, request: MovimentoRequest
    ) -> MovimentoResultado:
        response = await self._request(
            "POST",
            "/estoque/movimentos",
            headers={"Idempotency-Key": idempotency_key},
            json=request.model_dump(mode="json", exclude_none=True),
        )
        self._raise_for_error(response)
        return MovimentoResultado.model_validate(response.json())

    async def listar_agenda(self, de: datetime, ate: datetime) -> list[AgendaItem]:
        params = {"de": de.isoformat(), "ate": ate.isoformat()}
        response = await self._request("GET", "/notificacoes/agenda", params=params)
        self._raise_for_error(response)
        return [AgendaItem.model_validate(item) for item in response.json()]

    async def confirmar_envio(
        self, notification_id: int, request: ConfirmacaoEnvioRequest
    ) -> None:
        response = await self._request(
            "POST",
            f"/notificacoes/{notification_id}/confirmacao-envio",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        self._raise_for_error(response)

    async def listar_categorias(self) -> list[CategoriaInsumo]:
        """Read the supply categories (GET /categorias-insumo). The flow uses
        them to classify new supplies; the ops layer caches the
        result with a short TTL so the catalog classification does not hit the
        core on every message."""
        response = await self._request("GET", "/categorias-insumo")
        self._raise_for_error(response)
        return [CategoriaInsumo.model_validate(item) for item in response.json()]

    async def criar_notificacao(self, request: NotificacaoRequest) -> None:
        """Create an in-app notification (POST /notificacoes). Used for the
        COMPRA_REGISTRADA record and the ALERTA_OPERACIONAL copy;
        the core defaults the status to NAO_ENVIAR, so these are in-app only
        unless the core schedules them."""
        response = await self._request(
            "POST", "/notificacoes", json=request.model_dump(exclude_none=True)
        )
        self._raise_for_error(response)
