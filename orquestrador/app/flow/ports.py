"""Narrow ports the flow layer depends on.

The application layer never imports the concrete Redis store or an LLM SDK: it
talks to these protocols so the same handler runs against the real
`ConversationState` in production and against in-memory fakes in tests, with no
network. `ConversationState` already satisfies `StatePort` structurally.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.flow.intent import ReplyIntent


@runtime_checkable
class StatePort(Protocol):
    """Conversation and item state the flow reads and writes.

    This is the subset of `app.state.store.ConversationState` the flow uses.
    Keeping it narrow lets tests provide an in-memory double without Redis.
    """

    async def mark_inbound(self, message_id: str) -> bool:
        """Record an inbound id; return False when it was already seen."""
        ...

    async def acquire_lock(self, sender: str) -> bool:
        """Take the per-conversation lock; return False when already held."""
        ...

    async def release_lock(self, sender: str) -> None: ...

    async def save_conversation(self, sender: str, fields: dict[str, object]) -> None: ...

    async def load_conversation(self, sender: str) -> dict[str, object]: ...

    async def clear_conversation(self, sender: str) -> None: ...

    async def set_item_status(
        self, fluxo_id: str, item_key: str, status: dict[str, object]
    ) -> None: ...

    async def get_item_status(
        self, fluxo_id: str, item_key: str
    ) -> dict[str, object] | None: ...

    async def all_item_status(self, fluxo_id: str) -> dict[str, object]: ...


@runtime_checkable
class ClassifierPort(Protocol):
    """Classifies a free-text reply during a flow into one intent.

    Implemented over the LLM in production and faked in tests. Kept separate
    from `LlmPort` so the handler depends only on the small shape it needs.
    """

    async def classify(self, text: str) -> ReplyIntent: ...
