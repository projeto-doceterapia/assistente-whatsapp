from __future__ import annotations

# Redis key scheme for the conversation state. Builders are the single source
# of key names so the whole service agrees on them.

DEDUPE_TTL_S = 72 * 3600
SENT_TTL_S = 7 * 24 * 3600
LOCK_TTL_S = 30
FLOW_ITEMS_TTL_S = 24 * 3600
DLQ_TTL_S = 30 * 24 * 3600
NOTIF_TTL_S = 7 * 24 * 3600


def conv(sender: str) -> str:
    return f"conv:{sender}"


def media(fluxo_id: str) -> str:
    return f"media:{fluxo_id}"


def dedupe_in(message_id: str) -> str:
    return f"dedupe:in:{message_id}"


def sent(message_id: str) -> str:
    return f"sent:{message_id}"


def lock_conv(sender: str) -> str:
    return f"lock:conv:{sender}"


def flow_items(fluxo_id: str) -> str:
    return f"flow:{fluxo_id}:items"


def retry_queue() -> str:
    return "retry:queue"


def retry_member(fluxo_id: str, item_key: str) -> str:
    return f"{fluxo_id}:{item_key}"


def dlq_items() -> str:
    return "dlq:items"


def notif(notification_id: str) -> str:
    return f"notif:{notification_id}"


def notif_schedule() -> str:
    return "notif:schedule"
