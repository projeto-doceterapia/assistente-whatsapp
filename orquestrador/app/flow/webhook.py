"""FastAPI webhook routes for the selected channel.

Baileys delivers signed POSTs: the route authenticates with HMAC through the
channel and runs the handler inline. The Cloud API needs a GET verification
handshake and must answer 200 to a POST immediately, then process out of band,
so its POST schedules the handler on a background task.

The routes depend only on a `MessageProcessor` callable, so `main.py` wires the
real handler and tests pass a recording double. Signature failures return 401
and the handshake failure returns 403, so a bad caller never reaches the flow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.channel.port import InboundMessage
from app.channels.baileys import BaileysChannel, InvalidSignatureError as BaileysBadSignature
from app.channels.cloud_api import CloudApiChannel, InvalidSignatureError as CloudApiBadSignature

MessageProcessor = Callable[[InboundMessage], Awaitable[None]]

_BAILEYS_SIGNATURE = "X-Signature-256"
_CLOUD_SIGNATURE = "X-Hub-Signature-256"


def baileys_router(channel: BaileysChannel, process: MessageProcessor) -> APIRouter:
    """Router for the Baileys adapter: one signed POST, processed inline."""
    router = APIRouter()

    @router.post("/webhook")
    async def webhook(request: Request) -> Response:
        body = await request.body()
        signature = request.headers.get(_BAILEYS_SIGNATURE)
        try:
            message = channel.verify_and_parse(body, signature)
        except BaileysBadSignature:
            return Response(status_code=401)
        if message is not None:
            await process(message)
        return Response(status_code=200)

    return router


def cloud_api_router(channel: CloudApiChannel, process: MessageProcessor) -> APIRouter:
    """Router for the Meta Cloud API: GET handshake, POST acked immediately."""
    router = APIRouter()

    @router.get("/webhook")
    async def verify(request: Request) -> Response:
        params = request.query_params
        try:
            challenge = channel.verify_handshake(
                params.get("hub.mode"),
                params.get("hub.verify_token"),
                params.get("hub.challenge"),
            )
        except CloudApiBadSignature:
            return Response(status_code=403)
        return Response(content=challenge, media_type="text/plain")

    @router.post("/webhook")
    async def webhook(request: Request, background: BackgroundTasks) -> Response:
        body = await request.body()
        signature = request.headers.get(_CLOUD_SIGNATURE)
        try:
            messages = channel.parse_event(body, signature)
        except CloudApiBadSignature:
            return Response(status_code=401)
        # Answer 200 right away; process each message out of band.
        for message in messages:
            background.add_task(process, message)
        return Response(status_code=200)

    return router


def webhook_router(
    channel_name: str,
    channel: object,
    process: MessageProcessor,
) -> APIRouter:
    """Pick the router for the configured channel (CHANNEL)."""
    if channel_name == "cloud_api":
        assert isinstance(channel, CloudApiChannel)
        return cloud_api_router(channel, process)
    assert isinstance(channel, BaileysChannel)
    return baileys_router(channel, process)
