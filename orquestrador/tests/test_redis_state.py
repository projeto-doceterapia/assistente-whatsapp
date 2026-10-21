from __future__ import annotations

import os
import uuid

import pytest

from app.state.store import ConversationState

REDIS_TEST_URL = os.environ.get("REDIS_TEST_URL")

pytestmark = pytest.mark.skipif(
    not REDIS_TEST_URL, reason="REDIS_TEST_URL not set; needs a real Redis container"
)


@pytest.fixture
async def store() -> ConversationState:
    state = ConversationState.from_url(REDIS_TEST_URL, conv_ttl_min=30)
    yield state
    await state.aclose()


def _sender() -> str:
    return f"55119{uuid.uuid4().hex[:8]}"


async def test_conversation_hash_roundtrip_and_ttl(store: ConversationState) -> None:
    sender = _sender()
    await store.save_conversation(
        sender, {"estado": "AGUARDANDO_CONFIRMACAO", "ajustes": 1, "rascunho": {"itens": [1, 2]}}
    )
    loaded = await store.load_conversation(sender)
    assert loaded["estado"] == "AGUARDANDO_CONFIRMACAO"
    assert loaded["ajustes"] == 1
    assert loaded["rascunho"] == {"itens": [1, 2]}
    ttl = await store.conversation_ttl(sender)
    assert 0 < ttl <= 30 * 60
    await store.clear_conversation(sender)
    assert await store.load_conversation(sender) == {}


async def test_inbound_dedup_first_wins(store: ConversationState) -> None:
    message_id = f"m-{uuid.uuid4().hex}"
    assert await store.mark_inbound(message_id) is True
    assert await store.mark_inbound(message_id) is False


async def test_conversation_lock_is_exclusive(store: ConversationState) -> None:
    sender = _sender()
    assert await store.acquire_lock(sender) is True
    assert await store.acquire_lock(sender) is False
    await store.release_lock(sender)
    assert await store.acquire_lock(sender) is True
    await store.release_lock(sender)


async def test_sent_ids_echo_filter(store: ConversationState) -> None:
    message_id = f"out-{uuid.uuid4().hex}"
    assert await store.was_sent(message_id) is False
    await store.mark_sent(message_id)
    assert await store.was_sent(message_id) is True


async def test_item_status_isolated_per_item(store: ConversationState) -> None:
    fluxo_id = f"fluxo-{uuid.uuid4().hex}"
    await store.set_item_status(fluxo_id, "insumo:1", {"status": "OK", "tentativas": 1})
    await store.set_item_status(fluxo_id, "insumo:2", {"status": "FALHA", "tentativas": 3})
    assert (await store.get_item_status(fluxo_id, "insumo:1"))["status"] == "OK"
    todos = await store.all_item_status(fluxo_id)
    assert set(todos.keys()) == {"insumo:1", "insumo:2"}
    assert todos["insumo:2"]["tentativas"] == 3


async def test_retry_sorted_set_returns_due_only(store: ConversationState) -> None:
    fluxo_id = f"fluxo-{uuid.uuid4().hex}"
    await store.schedule_retry(fluxo_id, "insumo:1", when_s=100.0)
    await store.schedule_retry(fluxo_id, "insumo:2", when_s=500.0)
    due = await store.due_retries(now_s=200.0)
    member_1 = f"{fluxo_id}:insumo:1"
    member_2 = f"{fluxo_id}:insumo:2"
    assert member_1 in due
    assert member_2 not in due
    await store.drop_retry(fluxo_id, "insumo:1")
    assert f"{fluxo_id}:insumo:1" not in await store.due_retries(now_s=1000.0)
    await store.drop_retry(fluxo_id, "insumo:2")


async def test_dlq_appends_entries(store: ConversationState) -> None:
    before = await store.dlq_size()
    entry = {"fluxo_id": f"fluxo-{uuid.uuid4().hex}", "item_key": "insumo:9", "erro": "validation"}
    await store.push_dlq(entry)
    assert await store.dlq_size() == before + 1
    assert entry in await store.read_dlq()
