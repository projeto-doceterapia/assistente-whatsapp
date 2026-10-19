from __future__ import annotations

import asyncio


class FakeRedis:
    """Minimal in-memory async Redis for the delivery and retry state layers.

    Covers the string ops the delivery state uses (get, set with nx/ex, exists,
    delete) plus the sorted-set, list and counter ops the retry state uses (zadd,
    zrangebyscore, zrem, rpush, llen, lrange, expire, decr). A lock serializes
    the mutating ops so the concurrent-worker tests exercise the same claims as
    real Redis. TTLs are accepted but not expired on a timer; the retry tests
    model expiry through the injected clock, not Redis TTL."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._lists: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    # --- strings ----------------------------------------------------------

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        async with self._lock:
            if nx and key in self._data:
                return None
            self._data[key] = value
            return True

    async def exists(self, key: str) -> int:
        return 1 if key in self._data else 0

    async def delete(self, key: str) -> int:
        async with self._lock:
            existed = key in self._data
            self._data.pop(key, None)
            self._zsets.pop(key, None)
            self._lists.pop(key, None)
            return 1 if existed else 0

    async def decr(self, key: str) -> int:
        async with self._lock:
            current = int(self._data.get(key, "0")) - 1
            self._data[key] = str(current)
            return current

    async def expire(self, key: str, seconds: int) -> bool:
        # TTLs are a no-op in the fake; presence is what the tests assert.
        return key in self._data or key in self._zsets or key in self._lists

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        """Run the small Lua scripts the state layers ship, atomically under the
        lock so the crash/double-completion tests exercise the same atomicity as
        real Redis. Two scripts are recognized: the pending-counter decrement
        (key only) and the delivery mark-sent compare-and-set (key plus the id,
        the claim prefix and a TTL).
        """
        key = keys_and_args[0]
        args = keys_and_args[numkeys:]
        async with self._lock:
            if len(args) >= 2:
                return self._eval_mark_sent(key, args)
            return self._eval_decr_pending(key)

    def _eval_decr_pending(self, key: str) -> int:
        if key not in self._data:
            return -1
        remaining = int(self._data[key]) - 1
        if remaining <= 0:
            self._data.pop(key, None)
            return 0
        self._data[key] = str(remaining)
        return remaining

    def _eval_mark_sent(self, key: str, args: tuple[str, ...]) -> int:
        new_value, claim_prefix = args[0], args[1]
        current = self._data.get(key)
        if current is None or not current.startswith(claim_prefix):
            return 0
        self._data[key] = new_value
        return 1

    # --- sorted sets ------------------------------------------------------

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        async with self._lock:
            zset = self._zsets.setdefault(key, {})
            added = sum(1 for member in mapping if member not in zset)
            zset.update(mapping)
            return added

    async def zrangebyscore(
        self,
        key: str,
        min_score: str | float,
        max_score: str | float,
        *,
        start: int = 0,
        num: int | None = None,
    ) -> list[str]:
        zset = self._zsets.get(key, {})
        lo = float("-inf") if min_score == "-inf" else float(min_score)
        hi = float("inf") if max_score == "+inf" else float(max_score)
        members = sorted(
            (m for m, score in zset.items() if lo <= score <= hi),
            key=lambda m: zset[m],
        )
        window = members[start:]
        if num is not None:
            window = window[:num]
        return window

    async def zrem(self, key: str, member: str) -> int:
        async with self._lock:
            zset = self._zsets.get(key, {})
            existed = member in zset
            zset.pop(member, None)
            return 1 if existed else 0

    async def zscore(self, key: str, member: str) -> float | None:
        return self._zsets.get(key, {}).get(member)

    # --- lists ------------------------------------------------------------

    async def rpush(self, key: str, value: str) -> int:
        async with self._lock:
            items = self._lists.setdefault(key, [])
            items.append(value)
            return len(items)

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self._lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]
