from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone

import httpx

from app.channel.port import (
    ChannelPort,
    HealthStatus,
    InboundMessage,
    MessageKind,
)
from app.channels.media_download import download_capped
from app.channels.settings import BaileysSettings
from app.templates import TemplateRenderer, load_default_renderer

# Header and signature shape as implemented by the adapter (adaptador-baileys
# src/hmac.ts and src/server.ts): events are signed HMAC-SHA256 over the raw
# body as "sha256=<hex>" in X-Signature-256; inbound endpoints require the
# shared secret in X-Adapter-Secret.
_SIGNATURE_HEADER = "X-Signature-256"
_ADAPTER_SECRET_HEADER = "X-Adapter-Secret"
_SIGNATURE_PREFIX = "sha256="

_HEALTH_BY_STATE = {
    "connected": HealthStatus.CONNECTED,
    "degraded": HealthStatus.DEGRADED,
    "down": HealthStatus.DOWN,
}

_KIND_BY_NAME = {
    "text": MessageKind.TEXT,
    "image": MessageKind.IMAGE,
    "unsupported": MessageKind.UNSUPPORTED,
}


class InvalidSignatureError(RuntimeError):
    """Raised when an inbound adapter event has a missing or wrong signature."""


class BaileysChannel(ChannelPort):
    """Channel backed by the Baileys adapter HTTP API.

    Outbound messages and media go over httpx to the adapter. Inbound events
    arrive on the orchestrator webhook; `verify_and_parse` authenticates them
    with HMAC and normalizes `message` events, dropping the assistant's own
    echoes (`fromMe`).
    """

    def __init__(
        self,
        settings: BaileysSettings,
        client: httpx.AsyncClient,
        renderer: TemplateRenderer | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._renderer = renderer or load_default_renderer()

    @classmethod
    def from_settings(cls, settings: BaileysSettings) -> "BaileysChannel":
        client = httpx.AsyncClient(
            base_url=settings.adapter_url,
            headers={_ADAPTER_SECRET_HEADER: settings.adapter_secret},
        )
        return cls(settings, client)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_text(self, recipient: str, text: str) -> str:
        # The adapter always targets its dedicated group and answers 202 without
        # a message id (src/server.ts), so the channel mints a local id.
        response = await self._client.post("/messages", json={"text": text})
        response.raise_for_status()
        return f"baileys-{uuid.uuid4().hex}"

    async def send_notification(
        self, recipient: str, template_key: str, parametros: dict[str, object]
    ) -> str:
        text = self._renderer.render_text(template_key, parametros)
        return await self.send_text(recipient, text)

    async def fetch_media(self, media_ref: str) -> bytes:
        return await download_capped(
            self._client,
            f"/media/{media_ref}",
            media_ref=media_ref,
            max_bytes=self._settings.media_max_bytes,
        )

    async def health(self) -> HealthStatus:
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
        except httpx.HTTPError:
            return HealthStatus.DOWN
        state = str(response.json().get("status", "")).lower()
        return _HEALTH_BY_STATE.get(state, HealthStatus.DOWN)

    def verify_and_parse(self, body: bytes, signature: str | None) -> InboundMessage | None:
        """Authenticate a raw webhook body and normalize a message event.

        Returns None for non-message events and for the assistant's own echoes.
        Raises InvalidSignatureError when the HMAC does not match.
        """
        if not self._signature_ok(body, signature):
            raise InvalidSignatureError("invalid webhook signature")
        event = httpx.Response(200, content=body).json()
        if event.get("event") != "message":
            return None
        if event.get("fromMe"):
            return None
        return self._normalize(event)

    def _signature_ok(self, body: bytes, signature: str | None) -> bool:
        if not signature or not signature.startswith(_SIGNATURE_PREFIX):
            return False
        expected = self._sign(body)
        return hmac.compare_digest(expected, signature)

    def _sign(self, body: bytes) -> str:
        digest = hmac.new(
            self._settings.webhook_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return f"{_SIGNATURE_PREFIX}{digest}"

    @staticmethod
    def _normalize(event: dict[str, object]) -> InboundMessage:
        kind = _KIND_BY_NAME.get(str(event.get("kind")), MessageKind.UNSUPPORTED)
        text = event.get("text")
        media_ref = event.get("mediaRef")
        timestamp = _to_datetime(event.get("timestamp"))
        return InboundMessage(
            id=str(event.get("id")),
            sender=_to_e164(str(event.get("senderJid", ""))),
            kind=kind,
            timestamp=timestamp,
            text=text if (kind is MessageKind.TEXT and text) else None,
            media_ref=str(media_ref) if (kind is MessageKind.IMAGE and media_ref) else None,
        )


def _to_datetime(raw: object) -> datetime:
    # The adapter stamps events with epoch milliseconds (src/types.ts).
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


def _to_e164(jid: str) -> str:
    # WhatsApp JIDs look like "<number>@s.whatsapp.net"; keep only the number.
    return jid.split("@", 1)[0].split(":", 1)[0]
