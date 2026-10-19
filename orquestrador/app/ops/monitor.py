"""Background health monitor and failure counters.

The monitor is a single asyncio task owned by the application, mirroring the
delivery worker's lifecycle (`start`/`stop`). Each poll checks three liveness
signals and lets `OperationalAlerts` decide, with its own cool-down, whether to
raise:
  - the WhatsApp session, via `ChannelPort.health()` (session down);
  - Redis, via `ping()` (Redis down);
  - the core, via a lightweight read, counting consecutive failures so a single
    blip does not alert (core down after a threshold).

AI errors and recording-path core failures are counted through the explicit
`record_ai_error`/`record_core_failure` methods, so the flow can feed them
without importing the ops package internals.
"""

from __future__ import annotations

import asyncio
import logging

from app.channel.port import ChannelPort, HealthStatus
from app.core.client import CoreClient
from app.core.errors import CoreError
from app.ops.alerts import OperationalAlerts
from app.ops.settings import OpsSettings

logger = logging.getLogger("ops.monitor")


class HealthMonitor:
    """Polls liveness signals and raises operational alerts through
    `OperationalAlerts`. It keeps consecutive-failure counters for the core and
    the AI so a threshold, not a single error, triggers the alert."""

    def __init__(
        self,
        *,
        alerts: OperationalAlerts,
        channel: ChannelPort,
        core: CoreClient,
        redis: object,
        settings: OpsSettings | None = None,
    ) -> None:
        self._alerts = alerts
        self._channel = channel
        self._core = core
        self._redis = redis
        self._settings = settings or OpsSettings()
        self._core_failures = 0
        self._ai_failures = 0
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ops-health-monitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:  # a bad poll must not kill the loop
                logger.exception("ops_monitor_poll_failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.health_poll_interval_s
                )
            except asyncio.TimeoutError:
                continue

    async def poll_once(self) -> None:
        """One liveness sweep. Public so tests drive it without the sleep loop."""
        await self._check_session()
        await self._check_redis()
        await self._check_core()

    async def _check_session(self) -> None:
        try:
            health = await self._channel.health()
        except Exception:
            logger.exception("ops_monitor_health_check_failed")
            await self._alerts.raise_session_down()
            return
        if health is HealthStatus.DOWN:
            await self._alerts.raise_session_down()

    async def _check_redis(self) -> None:
        ping = getattr(self._redis, "ping", None)
        if ping is None:
            return
        try:
            await ping()
        except Exception:
            # Redis is the alert cool-down store too; OperationalAlerts fails
            # open when it cannot reach Redis, so the alert still goes out.
            logger.warning("ops_monitor_redis_down")
            await self._alerts.raise_redis_down()

    async def _check_core(self) -> None:
        try:
            await self._core.listar_categorias()
        except CoreError:
            await self.record_core_failure()
            return
        await self.record_core_success()

    # --- explicit counters fed by other workers ---------------------------

    async def record_core_failure(self) -> None:
        self._core_failures += 1
        if self._core_failures >= self._settings.core_failure_threshold:
            await self._alerts.raise_core_down()

    async def record_core_success(self) -> None:
        self._core_failures = 0

    async def record_ai_error(self) -> None:
        self._ai_failures += 1
        if self._ai_failures >= self._settings.ai_failure_threshold:
            await self._alerts.raise_ai_errors()

    async def record_ai_success(self) -> None:
        self._ai_failures = 0
