from __future__ import annotations

import asyncio


class FakeRedis:
    """Minimal in-memory async Redis covering the string ops the delivery state
    uses (get, set with nx/ex, exists, delete, eval). A lock serializes SET NX
    and the mark-sent Lua so a concurrent-worker test exercises the same
    exactly-once guard as real Redis."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = asyncio.Lock()

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
        existed = key in self._data
        self._data.pop(key, None)
        return 1 if existed else 0

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        """Run the delivery mark-sent compare-and-set atomically under the lock,
        so the crash/double-completion tests exercise the same atomicity as real
        Redis. The script takes the key plus the id, the claim prefix and a TTL.
        """
        key = keys_and_args[0]
        args = keys_and_args[numkeys:]
        async with self._lock:
            return self._eval_mark_sent(key, args)

    def _eval_mark_sent(self, key: str, args: tuple[str, ...]) -> int:
        new_value, claim_prefix = args[0], args[1]
        current = self._data.get(key)
        if current is None or not current.startswith(claim_prefix):
            return 0
        self._data[key] = new_value
        return 1
