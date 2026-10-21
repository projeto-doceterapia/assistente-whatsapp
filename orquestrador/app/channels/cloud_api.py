from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.channel.port import (
    ChannelPort,
    HealthStatus,
    InboundMessage,
    MediaNotFoundError,
    MessageKind,
)
from app.channels.media_download import download_capped
from app.channels.settings import CloudApiSettings
from app.templates import TemplateRenderer, load_default_renderer

# Meta signs every webhook body with HMAC-SHA256 over the raw bytes using the
# app secret, sent as "sha256=<hex>" in this header.
_SIGNATURE_HEADER = "X-Hub-Signature-256"
_SIGNATURE_PREFIX = "sha256="

_GRAPH_BASE = "https://graph.facebook.com"

_KIND_BY_TYPE = {
    "text": MessageKind.TEXT,
    "image": MessageKind.IMAGE,
}


class InvalidSignatureError(RuntimeError):
    """Raised when a Cloud API webhook body fails HMAC verification."""


class CloudApiChannel(ChannelPort):
    """Channel backed by the Meta WhatsApp Cloud API (Graph API).

    Sends free text and approved templates, downloads media in the documented
    two-step flow, answers the webhook verification handshake and verifies the
    X-Hub-Signature-256 of every event. `parse_event` returns the normalized
    message so the caller can answer 200 immediately and process out of band.
    """

    def __init__(
        self,
        settings: CloudApiSettings,
        client: httpx.AsyncClient,
        renderer: TemplateRenderer | None = None,
        media_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._renderer = renderer or load_default_renderer()
        self._base = f"/{settings.graph_version}"
        # The CDN download runs on a client that carries NO Authorization header,
        # so the Graph bearer token is never sent to the media host.
        self._media_client = media_client or httpx.AsyncClient()

    @classmethod
    def from_settings(cls, settings: CloudApiSettings) -> "CloudApiChannel":
        client = httpx.AsyncClient(
            base_url=_GRAPH_BASE,
            headers={"Authorization": f"Bearer {settings.access_token}"},
        )
        media_client = httpx.AsyncClient()
        return cls(settings, client, media_client=media_client)

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._media_client.aclose()

    async def send_text(self, recipient: str, text: str) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": text},
        }
        return await self._post_message(payload)

    async def send_notification(
        self, recipient: str, template_key: str, parametros: dict[str, object]
    ) -> str:
        rendered = self._renderer.render(template_key, parametros)
        # Cloud API always sends the approved template with parameters, never
        # free text; parts are newline-free so Meta accepts them as body params.
        parameters = [{"type": "text", "text": part} for part in rendered.parts]
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": rendered.cloud_name,
                "language": {"code": rendered.cloud_lang},
                "components": [{"type": "body", "parameters": parameters}],
            },
        }
        return await self._post_message(payload)

    async def fetch_media(self, media_ref: str) -> bytes:
        # Step 1: resolve the media id to a short-lived download URL. This call
        # stays on the authenticated Graph client.
        meta = await self._client.get(f"{self._base}/{media_ref}")
        if meta.status_code == 404:
            raise MediaNotFoundError(media_ref)
        meta.raise_for_status()
        url = meta.json().get("url")
        if not url:
            raise MediaNotFoundError(media_ref)
        # Step 2: download the bytes from the CDN URL Meta returned. The bearer
        # token is NEVER sent here: a host that is not a trusted Meta CDN could
        # otherwise receive it. The URL must be HTTPS and its host must match an
        # allowed suffix; the body is streamed under the configured byte cap.
        self._require_trusted_media_url(url, media_ref)
        return await download_capped(
            self._media_client,
            url,
            media_ref=media_ref,
            max_bytes=self._settings.media_max_bytes,
        )

    def _require_trusted_media_url(self, url: str, media_ref: str) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            raise UntrustedMediaHostError(media_ref, url)
        allowed = any(
            host == suffix.lstrip(".") or host.endswith(suffix)
            for suffix in self._settings.media_hosts
        )
        if not allowed:
            raise UntrustedMediaHostError(media_ref, url)

    async def health(self) -> HealthStatus:
        try:
            response = await self._client.get(f"{self._base}/{self._settings.phone_number_id}")
            response.raise_for_status()
        except httpx.HTTPError:
            return HealthStatus.DOWN
        return HealthStatus.CONNECTED

    async def _post_message(self, payload: dict[str, object]) -> str:
        response = await self._client.post(
            f"{self._base}/{self._settings.phone_number_id}/messages", json=payload
        )
        response.raise_for_status()
        messages = response.json().get("messages") or []
        if not messages:
            raise CloudApiError("Cloud API returned no message id")
        return str(messages[0]["id"])

    def verify_handshake(self, mode: str | None, token: str | None, challenge: str | None) -> str:
        """Answer the GET verification handshake, returning the challenge echo."""
        if mode == "subscribe" and token and challenge and hmac.compare_digest(
            token, self._settings.verify_token
        ):
            return challenge
        raise InvalidSignatureError("verification handshake failed")

    def verify_signature(self, body: bytes, signature: str | None) -> None:
        if not signature or not signature.startswith(_SIGNATURE_PREFIX):
            raise InvalidSignatureError("missing webhook signature")
        digest = hmac.new(
            self._settings.app_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(f"{_SIGNATURE_PREFIX}{digest}", signature):
            raise InvalidSignatureError("invalid webhook signature")

    def parse_event(self, body: bytes, signature: str | None) -> list[InboundMessage]:
        """Verify and normalize every message in a webhook event.

        Status-only events (delivery receipts) yield an empty list. The caller
        answers 200 before doing anything with the returned messages.
        """
        self.verify_signature(body, signature)
        event = httpx.Response(200, content=body).json()
        messages: list[InboundMessage] = []
        for entry in event.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value", {}) or {}
                for message in value.get("messages", []) or []:
                    messages.append(_normalize(message))
        return messages


class CloudApiError(RuntimeError):
    """Raised when the Cloud API responds without the expected payload."""


class UntrustedMediaHostError(RuntimeError):
    """Raised when the media download URL is not a trusted Meta CDN over HTTPS.

    Refusing the download here guarantees the Graph bearer token is never sent
    to an unexpected host returned by the first step of the two-step flow.
    """

    def __init__(self, media_ref: str, url: str) -> None:
        super().__init__("media download URL is not a trusted HTTPS Meta host")
        self.media_ref = media_ref
        self.url = url


def _normalize(message: dict[str, object]) -> InboundMessage:
    msg_type = str(message.get("type"))
    kind = _KIND_BY_TYPE.get(msg_type, MessageKind.UNSUPPORTED)
    text = None
    media_ref = None
    if kind is MessageKind.TEXT:
        text = (message.get("text") or {}).get("body")
    elif kind is MessageKind.IMAGE:
        media_ref = (message.get("image") or {}).get("id")
    return InboundMessage(
        id=str(message.get("id")),
        sender=str(message.get("from", "")),
        kind=kind,
        timestamp=_to_datetime(message.get("timestamp")),
        text=text,
        media_ref=str(media_ref) if media_ref else None,
    )


def _to_datetime(raw: object) -> datetime:
    # Cloud API timestamps are epoch seconds encoded as strings.
    try:
        return datetime.fromtimestamp(int(str(raw)), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc)
