from __future__ import annotations

import json

from redis.asyncio import Redis

from app.state import keys


class ConversationState:
    """Operations over the Redis conversation state.

    All string values are JSON-encoded. The client is created with
    `decode_responses=True`, so reads return str. TTLs come from `keys`.
    """

    def __init__(self, redis: Redis, conv_ttl_min: int) -> None:
        self._redis = redis
        self._conv_ttl_s = conv_ttl_min * 60

    @classmethod
    def from_url(cls, url: str, conv_ttl_min: int) -> "ConversationState":
        return cls(Redis.from_url(url, decode_responses=True), conv_ttl_min)

    async def aclose(self) -> None:
        await self._redis.aclose()

    # conv:{sender} hash with TTL renewed on each write
    async def save_conversation(self, sender: str, fields: dict[str, object]) -> None:
        key = keys.conv(sender)
        mapping = {name: _encode(value) for name, value in fields.items()}
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, self._conv_ttl_s)
            await pipe.execute()

    async def load_conversation(self, sender: str) -> dict[str, object]:
        raw = await self._redis.hgetall(keys.conv(sender))
        return {name: _decode(value) for name, value in raw.items()}

    async def conversation_ttl(self, sender: str) -> int:
        return await self._redis.ttl(keys.conv(sender))

    async def clear_conversation(self, sender: str) -> None:
        await self._redis.delete(keys.conv(sender))

    # dedupe:in:{message_id} — first processing wins
    async def mark_inbound(self, message_id: str) -> bool:
        stored = await self._redis.set(
            keys.dedupe_in(message_id), "1", nx=True, ex=keys.DEDUPE_TTL_S
        )
        return bool(stored)

    # sent:{message_id} — own-message echo filter (Baileys)
    async def mark_sent(self, message_id: str) -> None:
        await self._redis.set(keys.sent(message_id), "1", ex=keys.SENT_TTL_S)

    async def was_sent(self, message_id: str) -> bool:
        return bool(await self._redis.exists(keys.sent(message_id)))

    # lock:conv:{sender} — NX lock serializing a conversation
    async def acquire_lock(self, sender: str) -> bool:
        acquired = await self._redis.set(
            keys.lock_conv(sender), "1", nx=True, ex=keys.LOCK_TTL_S
        )
        return bool(acquired)

    async def release_lock(self, sender: str) -> None:
        await self._redis.delete(keys.lock_conv(sender))

    # flow:{fluxo_id}:items — per-item recording status
    async def set_item_status(
        self, fluxo_id: str, item_key: str, status: dict[str, object]
    ) -> None:
        await self._redis.hset(keys.flow_items(fluxo_id), item_key, _encode(status))

    async def get_item_status(self, fluxo_id: str, item_key: str) -> dict[str, object] | None:
        raw = await self._redis.hget(keys.flow_items(fluxo_id), item_key)
        if raw is None:
            return None
        return _decode(raw)

    async def all_item_status(self, fluxo_id: str) -> dict[str, object]:
        raw = await self._redis.hgetall(keys.flow_items(fluxo_id))
        return {name: _decode(value) for name, value in raw.items()}

    async def expire_items(self, fluxo_id: str) -> None:
        await self._redis.expire(keys.flow_items(fluxo_id), keys.FLOW_ITEMS_TTL_S)

    # retry:queue — sorted set, score = next-attempt epoch seconds
    async def schedule_retry(self, fluxo_id: str, item_key: str, when_s: float) -> None:
        member = keys.retry_member(fluxo_id, item_key)
        await self._redis.zadd(keys.retry_queue(), {member: when_s})

    async def due_retries(self, now_s: float) -> list[str]:
        return await self._redis.zrangebyscore(keys.retry_queue(), "-inf", now_s)

    async def drop_retry(self, fluxo_id: str, item_key: str) -> None:
        await self._redis.zrem(keys.retry_queue(), keys.retry_member(fluxo_id, item_key))

    # dlq:items — list of definitively failed items
    async def push_dlq(self, entry: dict[str, object]) -> None:
        await self._redis.rpush(keys.dlq_items(), _encode(entry))
        await self._redis.expire(keys.dlq_items(), keys.DLQ_TTL_S)

    async def dlq_size(self) -> int:
        return await self._redis.llen(keys.dlq_items())

    async def read_dlq(self) -> list[dict[str, object]]:
        raw = await self._redis.lrange(keys.dlq_items(), 0, -1)
        return [_decode(value) for value in raw]


def _encode(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode(value: str) -> object:
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
