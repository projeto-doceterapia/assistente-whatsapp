from __future__ import annotations

from datetime import datetime
from enum import Enum

from app.state import keys

# TTL for delivery bookkeeping. It must outlive any window plus the time the
# service might stay down, so a restart still sees the chosen time and the sent
# guard. It matches the notification retention the core keeps (keys.NOTIF_TTL_S).
_DELIVERY_TTL_S = keys.NOTIF_TTL_S

# A claim stores this prefix plus the claim time (epoch seconds), so recovery
# can tell a claim apart from a recorded channel message id and measure its age.
_CLAIM_PREFIX = "claim:"

# Store the channel message id under the claim only while the claim marker is
# still there, so a late or duplicated writer never overwrites a different
# worker's outcome. Returns 1 on success, 0 when the slot no longer holds a
# claim marker.
_MARK_SENT_LUA = """
local current = redis.call('GET', KEYS[1])
if current == false then
  return 0
end
if string.sub(current, 1, string.len(ARGV[2])) ~= ARGV[2] then
  return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
"""


class ClaimStatus(str, Enum):
    """The state of a notification's send guard on recovery.

    NONE          -> never claimed; a fresh send may proceed.
    SENT          -> the channel returned an id; only confirmation is pending.
    CLAIMED_FRESH -> claimed without an id, but within the TTL; a send may still
                     be in flight, so wait and re-check on a later poll.
    CLAIMED_STALE -> claimed without an id past the TTL; the send outcome is
                     unknown and must be resolved deterministically.
    """

    NONE = "NONE"
    SENT = "SENT"
    CLAIMED_FRESH = "CLAIMED_FRESH"
    CLAIMED_STALE = "CLAIMED_STALE"


def schedule_key(notification_id: int) -> str:
    # Chosen send time for a notification. Reuses the existing notif:{id}
    # namespace so the whole service agrees on key names.
    return keys.notif(str(notification_id))


def sent_key(notification_id: int) -> str:
    # NX guard flipped before the channel call; its value is the claim marker
    # until the send returns, then the channel message id, so a later poll can
    # confirm without resending.
    return f"{keys.notif(str(notification_id))}:sent"


class DeliveryState:
    """Redis-backed delivery bookkeeping: the one-time chosen send time and the
    exactly-once send guard. It shares the service Redis client (decode_responses
    is on, so reads return str)."""

    def __init__(self, redis: object) -> None:
        self._redis = redis

    async def get_send_time(self, notification_id: int) -> datetime | None:
        raw = await self._redis.get(schedule_key(notification_id))
        if raw is None:
            return None
        return datetime.fromisoformat(raw)

    async def set_send_time_if_absent(
        self, notification_id: int, when: datetime
    ) -> datetime:
        """Persist the chosen send time only the first time (NX), and return the
        effective time. A concurrent writer that lost the race reads the winner's
        value, so every worker agrees on one send time."""
        stored = await self._redis.set(
            schedule_key(notification_id),
            when.isoformat(),
            nx=True,
            ex=_DELIVERY_TTL_S,
        )
        if stored:
            return when
        existing = await self.get_send_time(notification_id)
        # The key existed a moment ago; if it vanished (TTL) between SET NX and
        # GET, fall back to the time we proposed rather than failing.
        return existing if existing is not None else when

    async def claim_send(self, notification_id: int, claimed_at_s: float) -> bool:
        """Flip the exactly-once guard, stamping it with the claim time. Returns
        True for the single caller that wins; every other caller (concurrent
        worker, restart after a send) gets False and must not call the
        channel."""
        acquired = await self._redis.set(
            sent_key(notification_id),
            f"{_CLAIM_PREFIX}{claimed_at_s}",
            nx=True,
            ex=_DELIVERY_TTL_S,
        )
        return bool(acquired)

    async def mark_sent(self, notification_id: int, canal_message_id: str) -> None:
        """Record the channel message id on the guard, but only while it still
        holds the claim marker. This compare-and-set keeps the stored id
        consistent with the claim: a stale or duplicated writer cannot clobber
        an id another worker already recorded."""
        await self._redis.eval(
            _MARK_SENT_LUA,
            1,
            sent_key(notification_id),
            canal_message_id,
            _CLAIM_PREFIX,
            str(_DELIVERY_TTL_S),
        )

    async def sent_message_id(self, notification_id: int) -> str | None:
        """Return the stored channel message id when a send already happened, or
        None when the notification was never sent or is only claimed (the guard
        still holds the claim marker, not an id)."""
        value = await self._redis.get(sent_key(notification_id))
        if value is None or value == "" or value.startswith(_CLAIM_PREFIX):
            return None
        return value

    async def claim_status(
        self, notification_id: int, now_s: float, claim_ttl_s: int
    ) -> ClaimStatus:
        """Classify the send guard for recovery. A claim marker older than
        claim_ttl_s without an id is 'send outcome unknown' (CLAIMED_STALE)."""
        value = await self._redis.get(sent_key(notification_id))
        if value is None:
            return ClaimStatus.NONE
        if not value.startswith(_CLAIM_PREFIX):
            return ClaimStatus.SENT
        claimed_at = _claim_time(value)
        if claimed_at is not None and (now_s - claimed_at) > claim_ttl_s:
            return ClaimStatus.CLAIMED_STALE
        return ClaimStatus.CLAIMED_FRESH

    async def was_claimed(self, notification_id: int) -> bool:
        return bool(await self._redis.exists(sent_key(notification_id)))

    async def release_claim(self, notification_id: int) -> None:
        """Undo a claim when the channel call failed, so a later poll retries the
        send inside the still-open window."""
        await self._redis.delete(sent_key(notification_id))


def _claim_time(value: str) -> float | None:
    try:
        return float(value[len(_CLAIM_PREFIX) :])
    except ValueError:
        return None
