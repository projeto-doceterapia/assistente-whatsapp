"""In-memory `StatePort` double for tests (no Redis, no network).

`FakeState` mirrors the semantics the recorder and handler rely on: first-wins
inbound dedup, an exclusive per-conversation lock, a conversation hash and
isolated per-item status. It depends on nothing from the application layer, so
both the recording and flow tests can use it.
"""

from __future__ import annotations


class FakeState:
    """StatePort backed by dictionaries."""

    def __init__(self) -> None:
        self._inbound: set[str] = set()
        self._locks: set[str] = set()
        self._conversations: dict[str, dict[str, object]] = {}
        self._items: dict[str, dict[str, object]] = {}

    async def mark_inbound(self, message_id: str) -> bool:
        if message_id in self._inbound:
            return False
        self._inbound.add(message_id)
        return True

    async def acquire_lock(self, sender: str) -> bool:
        if sender in self._locks:
            return False
        self._locks.add(sender)
        return True

    async def release_lock(self, sender: str) -> None:
        self._locks.discard(sender)

    async def save_conversation(self, sender: str, fields: dict[str, object]) -> None:
        self._conversations[sender] = dict(fields)

    async def load_conversation(self, sender: str) -> dict[str, object]:
        return dict(self._conversations.get(sender, {}))

    async def clear_conversation(self, sender: str) -> None:
        self._conversations.pop(sender, None)

    async def set_item_status(
        self, fluxo_id: str, item_key: str, status: dict[str, object]
    ) -> None:
        self._items.setdefault(fluxo_id, {})[item_key] = dict(status)

    async def get_item_status(
        self, fluxo_id: str, item_key: str
    ) -> dict[str, object] | None:
        return self._items.get(fluxo_id, {}).get(item_key)

    async def all_item_status(self, fluxo_id: str) -> dict[str, object]:
        return dict(self._items.get(fluxo_id, {}))

    # Test helpers ---------------------------------------------------------
    def expire(self, sender: str) -> None:
        """Simulate the conversation TTL elapsing (30-min expiry)."""
        self._conversations.pop(sender, None)

    def seed_item(self, fluxo_id: str, item_key: str, status: dict[str, object]) -> None:
        self._items.setdefault(fluxo_id, {})[item_key] = dict(status)
