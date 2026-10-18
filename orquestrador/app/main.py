from __future__ import annotations

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.channel.port import InboundMessage
from app.channels import (
    BaileysChannel,
    BaileysSettings,
    ChannelSettingsError,
    CloudApiChannel,
    CloudApiSettings,
    channel_name,
)
from app.config import MissingSettingError, Settings
from app.core.client import CoreClient
from app.flow.handler import InboundHandler
from app.flow.intent import LlmClassifier
from app.flow.models import FlowDeps
from app.flow.webhook import webhook_router
from app.llm.gemini import GeminiProvider
from app.llm.port import GeminiSettings, LlmConfigError
from app.logging import configure_logging
from app.purchase.extracao import ExtracaoService
from app.purchase.rendering import Categoria
from app.state.store import ConversationState


class HealthResponse(BaseModel):
    status: str
    version: str


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="orquestrador", version=__version__)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    # The webhook needs channel, core, Redis and LLM credentials. They are not
    # present for a bare /health start (task 7.1), so wiring is best-effort:
    # when any setting is missing the app still serves /health.
    _try_attach_webhook(app)

    return app


def _try_attach_webhook(app: FastAPI) -> None:
    try:
        settings = Settings.from_env()
        channel = _build_channel()
        handler = _build_handler(settings, channel)
    except (MissingSettingError, ChannelSettingsError, LlmConfigError):
        # A missing integration setting only disables the webhook, never /health.
        return
    app.include_router(webhook_router(settings.channel, channel, handler.handle))


def _build_channel() -> object:
    name = channel_name()
    if name == "cloud_api":
        return CloudApiChannel.from_settings(CloudApiSettings.from_env())
    return BaileysChannel.from_settings(BaileysSettings.from_env())


def _build_handler(settings: Settings, channel: object) -> InboundHandler:
    core_client = CoreClient(httpx.AsyncClient(base_url=settings.core_base_url))
    llm = GeminiProvider(GeminiSettings.from_env())
    state = ConversationState.from_url(settings.redis_url, settings.conv_ttl_min)

    async def categorias() -> list[Categoria]:
        # The catalog carries no categories; they come from GET /categorias-insumo,
        # which the core client does not expose yet (task scope). Until then the
        # AI classifies against an empty list and items fall back to the default.
        return []

    deps = FlowDeps(
        state=state,
        channel=channel,  # type: ignore[arg-type]
        core=core_client,
        llm=llm,
        classifier=LlmClassifier(llm),
        extracao=ExtracaoService(llm, core_client),
        beneficiaria=settings.beneficiaria_telefone,
        categorias_provider=categorias,
        max_ajustes=settings.max_ajustes,
    )
    return InboundHandler(deps)


app = create_app()
