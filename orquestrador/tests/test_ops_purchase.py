from __future__ import annotations

import json

import pytest

from app.core.client import CoreClient
from app.core.models import StatusEnvioNotificacao, TipoNotificacao
from app.core.stub import CoreStub
from app.ops.purchase import PurchaseNotifier
from app.recording.models import ItemResult, ItemStatus, RecordingResult

NOTIF_PATH = "/notificacoes"


def _item(index: int, status: ItemStatus) -> ItemResult:
    return ItemResult(
        index=index,
        nome=f"item {index}",
        status=status,
        idempotency_key=f"fluxo:f:{index}",
    )


def _notif_bodies(stub: CoreStub) -> list[dict]:
    return [
        json.loads(req.content) for req in stub.requests if req.url.path == NOTIF_PATH
    ]


async def test_full_success_creates_compra_registrada() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    result = RecordingResult(itens=[_item(0, ItemStatus.OK), _item(1, ItemStatus.OK)])
    async with stub.client() as http:
        await PurchaseNotifier(CoreClient(http)).on_purchase_recorded(result)
    bodies = _notif_bodies(stub)
    assert len(bodies) == 1
    assert bodies[0]["tipo"] == TipoNotificacao.COMPRA_REGISTRADA.value
    assert "2 itens" in bodies[0]["mensagem"]
    # In-app only: the request carries no delivery status (core defaults it to
    # NAO_ENVIAR), so the body must not try to set an envio status.
    assert "statusEnvio" not in bodies[0]
    assert StatusEnvioNotificacao.NAO_ENVIAR.value not in json.dumps(bodies[0])


async def test_partial_success_creates_compra_registrada() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    result = RecordingResult(
        itens=[_item(0, ItemStatus.OK), _item(1, ItemStatus.RETRYABLE)]
    )
    async with stub.client() as http:
        await PurchaseNotifier(CoreClient(http)).on_purchase_recorded(result)
    bodies = _notif_bodies(stub)
    assert len(bodies) == 1
    assert "parcialmente" in bodies[0]["mensagem"]


async def test_total_failure_does_not_create_notification() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    result = RecordingResult(
        itens=[_item(0, ItemStatus.FAILED), _item(1, ItemStatus.RETRYABLE)]
    )
    async with stub.client() as http:
        await PurchaseNotifier(CoreClient(http)).on_purchase_recorded(result)
    assert _notif_bodies(stub) == []


async def test_empty_result_does_not_create_notification() -> None:
    stub = CoreStub()
    stub.json("POST", NOTIF_PATH, 201, {"idNotificacao": 1})
    async with stub.client() as http:
        await PurchaseNotifier(CoreClient(http)).on_purchase_recorded(
            RecordingResult(itens=[])
        )
    assert _notif_bodies(stub) == []


async def test_core_failure_is_swallowed() -> None:
    stub = CoreStub()
    stub.status("POST", NOTIF_PATH, 503)
    result = RecordingResult(itens=[_item(0, ItemStatus.OK)])
    async with stub.client() as http:
        # A recorded purchase is never undone because its in-app notice failed.
        await PurchaseNotifier(CoreClient(http)).on_purchase_recorded(result)
