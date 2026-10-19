from __future__ import annotations

import pytest

from app.channel.fake import FakeChannel
from app.channel.port import HealthStatus
from app.core.client import CoreClient
from app.core.stub import CoreStub
from app.ops.monitor import HealthMonitor
from app.ops.settings import OpsSettings


class SpyAlerts:
    """Records which typed alerts were raised, so the monitor's decisions are
    asserted without the core/channel side effects (covered by test_ops_alerts)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def raise_session_down(self) -> None:
        self.calls.append("session")

    async def raise_redis_down(self) -> None:
        self.calls.append("redis")

    async def raise_core_down(self) -> None:
        self.calls.append("core")

    async def raise_ai_errors(self) -> None:
        self.calls.append("ai")


class OkRedis:
    async def ping(self) -> bool:
        return True


class DownRedis:
    async def ping(self) -> bool:
        raise RuntimeError("redis down")


def _monitor(alerts, channel, http, redis, settings=None) -> HealthMonitor:
    return HealthMonitor(
        alerts=alerts,
        channel=channel,
        core=CoreClient(http),
        redis=redis,
        settings=settings or OpsSettings(),
    )


async def test_poll_session_down_raises_session_alert() -> None:
    stub = CoreStub()
    stub.json("GET", "/categorias-insumo", 200, [])
    channel = FakeChannel()
    channel.set_health(HealthStatus.DOWN)
    alerts = SpyAlerts()
    async with stub.client() as http:
        await _monitor(alerts, channel, http, OkRedis()).poll_once()
    assert "session" in alerts.calls


async def test_poll_redis_down_raises_redis_alert() -> None:
    stub = CoreStub()
    stub.json("GET", "/categorias-insumo", 200, [])
    channel = FakeChannel()
    channel.set_health(HealthStatus.CONNECTED)
    alerts = SpyAlerts()
    async with stub.client() as http:
        await _monitor(alerts, channel, http, DownRedis()).poll_once()
    assert "redis" in alerts.calls


async def test_poll_core_down_alerts_only_after_threshold() -> None:
    stub = CoreStub()
    stub.status("GET", "/categorias-insumo", 503)  # core down on every poll
    channel = FakeChannel()
    channel.set_health(HealthStatus.CONNECTED)
    alerts = SpyAlerts()
    settings = OpsSettings(core_failure_threshold=3)
    async with stub.client() as http:
        monitor = _monitor(alerts, channel, http, OkRedis(), settings)
        await monitor.poll_once()
        await monitor.poll_once()
        assert alerts.calls.count("core") == 0  # below threshold
        await monitor.poll_once()
    assert alerts.calls.count("core") == 1  # threshold reached


async def test_core_success_resets_failure_counter() -> None:
    stub = CoreStub()
    stub.json("GET", "/categorias-insumo", 200, [])
    channel = FakeChannel()
    channel.set_health(HealthStatus.CONNECTED)
    alerts = SpyAlerts()
    settings = OpsSettings(core_failure_threshold=2)
    async with stub.client() as http:
        monitor = _monitor(alerts, channel, http, OkRedis(), settings)
        await monitor.record_core_failure()
        await monitor.poll_once()  # core healthy -> resets
        await monitor.record_core_failure()
    assert alerts.calls.count("core") == 0


async def test_ai_errors_alert_after_threshold() -> None:
    stub = CoreStub()
    stub.json("GET", "/categorias-insumo", 200, [])
    channel = FakeChannel()
    alerts = SpyAlerts()
    settings = OpsSettings(ai_failure_threshold=2)
    async with stub.client() as http:
        monitor = _monitor(alerts, channel, http, OkRedis(), settings)
        await monitor.record_ai_error()
        assert alerts.calls.count("ai") == 0
        await monitor.record_ai_error()
    assert alerts.calls.count("ai") == 1
