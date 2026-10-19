"""Application entry point and wiring.

`create_app` builds the FastAPI app and, best effort, the full stack: the inbound
webhook plus the background workers (notification delivery, the persistent item
retry worker and the operational health monitor). When an integration setting is
missing the app still serves `/health` and starts no workers.

Wiring lives in small, single-purpose functions so each seam is isolated. The
retry worker starts and stops inside the lifespan, between the delivery worker
and the health monitor.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
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
from app.delivery import DeliveryWorker, create_delivery_worker
from app.flow.handler import InboundHandler
from app.flow.intent import LlmClassifier
from app.flow.models import FlowDeps
from app.flow.webhook import webhook_router
from app.llm.factory import build_llm_provider
from app.llm.port import LlmConfigError
from app.logging import configure_logging
from app.ops import (
    CategoriasProvider,
    HealthMonitor,
    OperationalAlerts,
    OpsSettings,
    PurchaseNotifier,
)
from app.purchase.extracao import ExtracaoService
from app.recording import Recorder
from app.retry import (
    RetryCoordinator,
    RetryWorker,
    create_retry_coordinator,
    create_retry_worker,
)
from app.state.store import ConversationState


class HealthResponse(BaseModel):
    status: str
    version: str


class Wiring:
    """The live object graph the app owns while running: the shared Redis-backed
    state, the HTTP client to the core, and the background workers. It is built
    once when every required integration setting is present, and torn down in
    the lifespan."""

    def __init__(
        self,
        *,
        state: ConversationState,
        http: httpx.AsyncClient,
        handler: InboundHandler,
        webhook_channel: str,
        channel: object,
        delivery: DeliveryWorker,
        retry: RetryWorker,
        monitor: HealthMonitor,
        purchases: PurchaseNotifier,
    ) -> None:
        self.state = state
        self.http = http
        self.handler = handler
        self.webhook_channel = webhook_channel
        self.channel = channel
        self.delivery = delivery
        self.retry = retry
        self.monitor = monitor
        self.purchases = purchases


def create_app() -> FastAPI:
    configure_logging()
    wiring = _build_wiring()
    app = FastAPI(title="orquestrador", version=__version__, lifespan=_lifespan(wiring))

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    if wiring is not None:
        app.include_router(
            webhook_router(wiring.webhook_channel, wiring.channel, wiring.handler.handle)
        )

    return app


def _lifespan(wiring: "Wiring | None"):
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if wiring is None:
            yield
            return
        await wiring.delivery.start()
        # <retry-worker-start> the persistent item retry worker replays failed
        # items and drives the final result message.
        await wiring.retry.start()
        await wiring.monitor.start()
        try:
            yield
        finally:
            await wiring.monitor.stop()
            await wiring.retry.stop()
            await wiring.delivery.stop()
            await wiring.http.aclose()
            await wiring.state.aclose()

    return lifespan


def _build_wiring() -> "Wiring | None":
    """Assemble the full stack, or return None when a required integration
    setting is missing so only `/health` runs.

    Once the channel, the core HTTP client and the Redis state are opened, the
    rest of the build runs under a guard: if it raises (a missing setting, an
    LLM misconfiguration), those already-opened resources are closed in reverse
    order before returning None, so a health-only startup never leaks an open
    httpx client or Redis connection."""
    try:
        settings = Settings.from_env()
        channel = _build_channel()
    except (MissingSettingError, ChannelSettingsError, LlmConfigError):
        return None

    http = httpx.AsyncClient(base_url=settings.core_base_url)
    state = ConversationState.from_url(settings.redis_url, settings.conv_ttl_min)
    try:
        return _assemble(settings, channel, http, state)
    except (MissingSettingError, LlmConfigError):
        # Close in the reverse order the resources were opened, so a partially
        # built stack leaves nothing open when the app falls back to health-only.
        _close_resources_best_effort(state, http, channel)
        return None


def _assemble(
    settings: Settings,
    channel: object,
    http: httpx.AsyncClient,
    state: ConversationState,
) -> "Wiring":
    """Wire every component over the already-opened channel, HTTP client and
    Redis state. Any build error propagates so the caller closes them."""
    ops_settings = OpsSettings.from_env()
    core = CoreClient(http)
    redis = _redis_of(state)

    alerts = OperationalAlerts(
        core=core,
        channel=channel,  # type: ignore[arg-type]
        redis=redis,
        recipient=settings.beneficiaria_telefone,
        settings=ops_settings,
    )

    coordinator = create_retry_coordinator(
        redis=redis,
        recipient=settings.beneficiaria_telefone,
    )
    purchases = PurchaseNotifier(core)
    monitor = HealthMonitor(
        alerts=alerts,
        channel=channel,  # type: ignore[arg-type]
        core=core,
        redis=redis,
        settings=ops_settings,
    )

    handler = _build_handler(
        settings, channel, core, state, coordinator, purchases, monitor
    )

    delivery = create_delivery_worker(
        core=core,
        channel=channel,  # type: ignore[arg-type]
        redis=redis,
        recipient=settings.beneficiaria_telefone,
        alerts=alerts,
    )
    retry = create_retry_worker(
        redis=redis,
        conversation=state,
        channel=channel,  # type: ignore[arg-type]
        replay=Recorder(state, core).record,
        coordinator=coordinator,
        alerts=alerts,
    )

    return Wiring(
        state=state,
        http=http,
        handler=handler,
        webhook_channel=settings.channel,
        channel=channel,
        delivery=delivery,
        retry=retry,
        monitor=monitor,
        purchases=purchases,
    )


def _close_resources_best_effort(*resources: object) -> None:
    """Close each resource that exposes `aclose`, swallowing per-resource errors
    so one failing close never hides the original build error. The coroutines
    are scheduled on the running loop when there is one; during synchronous
    app construction (no running loop) they are driven to completion here."""

    async def _close_all() -> None:
        for resource in resources:
            aclose = getattr(resource, "aclose", None)
            if aclose is None:
                continue
            try:
                await aclose()
            except Exception:  # best effort: a failing close must not mask the cause
                pass

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_close_all())
        return
    loop.create_task(_close_all())


def _build_channel() -> object:
    name = channel_name()
    if name == "cloud_api":
        return CloudApiChannel.from_settings(CloudApiSettings.from_env())
    return BaileysChannel.from_settings(BaileysSettings.from_env())


def _build_handler(
    settings: Settings,
    channel: object,
    core: CoreClient,
    state: ConversationState,
    coordinator: RetryCoordinator,
    purchases: PurchaseNotifier,
    monitor: HealthMonitor,
) -> InboundHandler:
    llm = build_llm_provider()
    categorias = CategoriasProvider(core, ttl_s=_ops_categorias_ttl())

    deps = FlowDeps(
        state=state,
        channel=channel,  # type: ignore[arg-type]
        core=core,
        llm=llm,
        classifier=LlmClassifier(llm),
        extracao=ExtracaoService(llm, core),
        beneficiaria=settings.beneficiaria_telefone,
        categorias_provider=categorias,
        max_ajustes=settings.max_ajustes,
    )
    # The same coordinator drives the retry worker, so a retryable failure in the
    # handler defers the final message to the worker. The
    # purchase notifier posts the in-app COMPRA_REGISTRADA after recording, and
    # the health monitor receives the AI/core failure counters.
    return InboundHandler(deps, coordinator, purchases, monitor)


def _ops_categorias_ttl() -> int:
    return OpsSettings.from_env().categorias_ttl_s


def _redis_of(state: ConversationState) -> object:
    """The delivery worker, the retry worker and the ops layer share the one
    Redis client the conversation state already opened, so the whole service
    uses a single pool."""
    return state._redis  # noqa: SLF001 - single, deliberate shared client


app = create_app()
