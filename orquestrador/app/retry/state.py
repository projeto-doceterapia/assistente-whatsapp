"""Redis-backed retry bookkeeping.

All retry state lives in Redis so the worker survives a process kill: the due
schedule (`retry:queue` sorted set, score = next-attempt epoch seconds), the
durable record per item (`retry:item:{member}`), a single-flight claim
(`retry:claim:{member}`, NX so two workers never process the same item at once),
a per-purchase pending counter that tells when the last item finished, the
static final-result snapshot and a per-member resolution (so concurrent workers
never clobber each other), and the on-request retry window per sender.

This layer wraps the raw Redis client (decode_responses is on, so reads return
str) and reuses the shared `retry:queue` / `dlq:items` key names from
`app.state.keys` so the whole service agrees on them.
"""

from __future__ import annotations

import json

from app.retry.models import RetryItem
from app.state import keys

# Retry records and bookkeeping outlive any backoff schedule and a long outage,
# but never longer than the DLQ retention, so stale state cannot pile up.
_RETRY_TTL_S = keys.DLQ_TTL_S
# The single-flight claim only needs to cover one resend attempt.
_CLAIM_TTL_S = 60

# Returned by decrement_pending when the counter is already gone, so a second
# (crashed or duplicated) completion is a no-op and never re-triggers the final
# message. It is distinct from 0, which marks the one real transition to zero.
ALREADY_COMPLETED = -1

# Decrement the pending counter idempotently in a single server-side step:
# a missing key returns the ALREADY_COMPLETED sentinel instead of recreating it
# at -1, and the key is deleted exactly when it reaches zero.
_DECR_PENDING_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return -1
end
local remaining = redis.call('DECR', KEYS[1])
if remaining <= 0 then
  redis.call('DEL', KEYS[1])
  return 0
end
return remaining
"""


def _item_key(member: str) -> str:
    return f"retry:item:{member}"


def _claim_key(member: str) -> str:
    return f"retry:claim:{member}"


def _pending_key(fluxo_id: str) -> str:
    return f"retry:pending:{fluxo_id}"


def _final_key(fluxo_id: str) -> str:
    return f"retry:final:{fluxo_id}"


def _resolved_key(fluxo_id: str, member: str) -> str:
    return f"retry:resolved:{member}"


def _request_key(sender: str) -> str:
    return f"retry:request:{sender}"


class RetryState:
    """Retry queue, durable records, claims, pending counts, the final-result
    snapshot and the on-request window, all in Redis."""

    def __init__(self, redis: object) -> None:
        self._redis = redis

    # --- schedule ---------------------------------------------------------

    async def schedule(self, item: RetryItem, when_s: float) -> None:
        """Persist the record and (re)schedule it at `when_s`. The record is
        written before the schedule entry, so a worker that sees the member in
        the queue always finds its record."""
        await self._redis.set(_item_key(item.member), item.model_dump_json(), ex=_RETRY_TTL_S)
        await self._redis.zadd(keys.retry_queue(), {item.member: when_s})

    async def due_members(self, now_s: float, limit: int = 50) -> list[str]:
        return await self._redis.zrangebyscore(
            keys.retry_queue(), "-inf", now_s, start=0, num=limit
        )

    async def load(self, member: str) -> RetryItem | None:
        raw = await self._redis.get(_item_key(member))
        if raw is None:
            return None
        return RetryItem.model_validate_json(raw)

    async def unschedule(self, member: str) -> None:
        """Remove only the schedule entry, keeping the record (e.g. succeeded, or
        moved to the on-request window)."""
        await self._redis.zrem(keys.retry_queue(), member)

    async def forget(self, member: str) -> None:
        """Remove the schedule entry and the record (no on-request replay)."""
        await self._redis.zrem(keys.retry_queue(), member)
        await self._redis.delete(_item_key(member))

    # --- single flight ----------------------------------------------------

    async def claim(self, member: str) -> bool:
        """Flip the per-item guard. Returns True for the single caller that wins;
        a racing worker gets False and must not process the item."""
        acquired = await self._redis.set(_claim_key(member), "1", nx=True, ex=_CLAIM_TTL_S)
        return bool(acquired)

    async def release_claim(self, member: str) -> None:
        await self._redis.delete(_claim_key(member))

    # --- DLQ --------------------------------------------------------------

    async def push_dlq(self, entry: dict[str, object]) -> None:
        await self._redis.rpush(keys.dlq_items(), json.dumps(entry, ensure_ascii=False))
        await self._redis.expire(keys.dlq_items(), keys.DLQ_TTL_S)

    async def dlq_size(self) -> int:
        return await self._redis.llen(keys.dlq_items())

    # --- pending counter (final result) -----------------------------------

    async def set_pending(self, fluxo_id: str, count: int) -> None:
        await self._redis.set(_pending_key(fluxo_id), str(count), ex=_RETRY_TTL_S)

    async def decrement_pending(self, fluxo_id: str) -> int:
        """Atomically decrement the pending counter and return the remaining
        count, or the ALREADY_COMPLETED sentinel when the counter is gone.

        The whole decision runs in one server-side step so a crashed or
        duplicated completion cannot drive the counter below zero: a missing key
        (already completed, or never set) returns the sentinel instead of
        recreating the key at -1, and the key is deleted exactly on the
        transition to zero. Only that single zero return means 'last item
        resolved', so the final message is composed at most once."""
        remaining = await self._redis.eval(_DECR_PENDING_LUA, 1, _pending_key(fluxo_id))
        return int(remaining)

    # --- final-result snapshot + per-member resolution --------------------

    async def save_final(self, fluxo_id: str, snapshot: dict[str, object]) -> None:
        await self._redis.set(
            _final_key(fluxo_id), json.dumps(snapshot, ensure_ascii=False), ex=_RETRY_TTL_S
        )

    async def load_final(self, fluxo_id: str) -> dict[str, object] | None:
        raw = await self._redis.get(_final_key(fluxo_id))
        if raw is None:
            return None
        return json.loads(raw)

    async def drop_final(self, fluxo_id: str) -> None:
        await self._redis.delete(_final_key(fluxo_id))

    async def resolve_member(
        self, fluxo_id: str, member: str, outcome: dict[str, object]
    ) -> None:
        """Record one member's final outcome in its own key, so concurrent
        workers never clobber a shared blob."""
        await self._redis.set(
            _resolved_key(fluxo_id, member),
            json.dumps(outcome, ensure_ascii=False),
            ex=_RETRY_TTL_S,
        )

    async def resolution(self, fluxo_id: str, member: str) -> dict[str, object] | None:
        raw = await self._redis.get(_resolved_key(fluxo_id, member))
        if raw is None:
            return None
        return json.loads(raw)

    async def drop_resolution(self, fluxo_id: str, member: str) -> None:
        await self._redis.delete(_resolved_key(fluxo_id, member))

    # --- on-request retry window ------------------------------------------

    async def open_request_window(
        self, sender: str, members: list[str], window_s: int, now_s: float
    ) -> None:
        """Record which members the owner may ask to retry and when the window
        closes. The Redis key is kept past the window (double the window) so an
        expired request is still observable and answered as "expired" instead of
        silently treated as a new interaction. An empty member list clears it."""
        if not members:
            await self._redis.delete(_request_key(sender))
            return
        payload = {"members": members, "expires_at": now_s + window_s}
        await self._redis.set(
            _request_key(sender),
            json.dumps(payload, ensure_ascii=False),
            ex=max(window_s * 2, window_s),
        )

    async def request_status(self, sender: str, now_s: float) -> tuple[str, list[str]]:
        """Return ('ACTIVE', members) while the window is open, ('EXPIRED', [])
        once it closed but the record lingers, or ('NONE', []) when nothing was
        ever offered."""
        raw = await self._redis.get(_request_key(sender))
        if raw is None:
            return "NONE", []
        payload = json.loads(raw)
        members = list(payload.get("members", []))
        if now_s >= float(payload.get("expires_at", 0)):
            return "EXPIRED", []
        return "ACTIVE", members

    async def close_request_window(self, sender: str) -> None:
        await self._redis.delete(_request_key(sender))

