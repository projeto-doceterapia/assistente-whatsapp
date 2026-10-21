from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta, timezone

from app.channel.port import ChannelPort
from app.core.client import CoreClient
from app.core.errors import CoreError
from app.core.models import (
    AgendaItem,
    ConfirmacaoEnvioRequest,
    StatusEnvioNotificacao,
)
from app.delivery.alerts import AlertRaiser
from app.delivery.clock import Clock, RandomSource, SystemClock, SystemRandom
from app.delivery.settings import DeliverySettings
from app.delivery.state import ClaimStatus, DeliveryState

logger = logging.getLogger("delivery")


class DeliveryWorker:
    """Polls the core agenda and delivers each pending notification exactly once,
    at a randomized time inside its window, then confirms the outcome to the core.

    The worker is a single asyncio task owned by the application. `main` builds it
    through `create_delivery_worker` and drives its lifecycle with `start`/`stop`.
    """

    def __init__(
        self,
        *,
        core: CoreClient,
        channel: ChannelPort,
        state: DeliveryState,
        recipient: str,
        settings: DeliverySettings,
        clock: Clock,
        random_source: RandomSource,
        alerts: AlertRaiser | None = None,
    ) -> None:
        self._core = core
        self._channel = channel
        self._state = state
        self._recipient = recipient
        self._settings = settings
        self._clock = clock
        self._random = random_source
        self._alerts = alerts
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="delivery-worker")

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
            except Exception:  # a single bad poll must not kill the loop
                logger.exception("delivery_poll_failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.poll_interval_s
                )
            except asyncio.TimeoutError:
                continue

    async def poll_once(self) -> None:
        """Run one agenda poll. Public so tests drive it deterministically without
        the sleep loop. A failed agenda read (e.g. the core is down) is a clean
        no-op: there is nothing to deliver until the core answers again."""
        now = self._clock.now()
        de = now - timedelta(minutes=self._settings.agenda_lookback_min)
        ate = now + timedelta(minutes=self._settings.agenda_horizon_min)
        try:
            agenda = await self._core.listar_agenda(de, ate)
        except CoreError:
            logger.warning("delivery_agenda_unavailable")
            return
        for item in agenda:
            try:
                await self._process(item)
            except Exception:
                logger.exception("delivery_item_failed", extra={"notificacao": item.idNotificacao})

    async def _process(self, item: AgendaItem) -> None:
        notification_id = item.idNotificacao
        # The core serializes the window as a LocalDateTime (UTC, no offset), so
        # it arrives naive; the clock is tz-aware UTC. Normalize the window to
        # tz-aware UTC before any comparison, otherwise mixing naive and aware
        # datetimes raises TypeError and the notification is never delivered.
        item = item.model_copy(
            update={
                "janelaInicio": _as_utc(item.janelaInicio),
                "janelaFim": _as_utc(item.janelaFim),
            }
        )

        # Recovery: a send already happened but the core still lists it as
        # pending, so confirmation never landed. Confirm again, never resend.
        already_sent = await self._state.sent_message_id(notification_id)
        if already_sent is not None:
            await self._confirm_sent(notification_id, already_sent)
            return

        # Recovery of an interrupted send: the guard was claimed but no channel
        # message id was ever recorded (a crash between claiming and recording).
        now_s = self._clock.now().timestamp()
        status = await self._state.claim_status(
            notification_id, now_s, self._settings.claim_ttl_s
        )
        if status is ClaimStatus.CLAIMED_FRESH:
            # A send may still be in flight; wait and re-check on a later poll.
            return
        if status is ClaimStatus.CLAIMED_STALE:
            # The send outcome is unknown past the claim TTL. The safest outcome
            # is to report FALHA and alert, rather than risk a duplicate send.
            await self._handle_outcome_unknown(notification_id)
            return

        send_time = _as_utc(
            await self._state.set_send_time_if_absent(
                notification_id, self._pick_send_time(item)
            )
        )
        now = self._clock.now()
        if now < send_time:
            return  # not due yet

        if now > item.janelaFim:
            await self._handle_window_closed(notification_id)
            return

        await self._send(item)

    def _pick_send_time(self, item: AgendaItem):
        span_s = (item.janelaFim - item.janelaInicio).total_seconds()
        if span_s <= 0:
            return item.janelaInicio
        offset_s = self._random.uniform(0.0, span_s)
        return item.janelaInicio + timedelta(seconds=offset_s)

    async def _send(self, item: AgendaItem) -> None:
        notification_id = item.idNotificacao
        # Exactly-once: the single caller that flips the guard sends; a racing
        # worker or a restart mid-send gets False and stops here. The guard is
        # stamped with the claim time so recovery can age an interrupted send.
        if not await self._state.claim_send(
            notification_id, self._clock.now().timestamp()
        ):
            return
        try:
            canal_message_id = await self._channel.send_notification(
                self._recipient, item.templateKey, _parse_parametros(item.parametros)
            )
        except Exception:
            # The channel failed before producing a message id: release the guard
            # so a later poll retries inside the still-open window.
            await self._state.release_claim(notification_id)
            logger.exception("delivery_send_failed", extra={"notificacao": notification_id})
            raise
        await self._state.mark_sent(notification_id, canal_message_id)
        logger.info("delivery_sent", extra={"notificacao": notification_id})
        await self._confirm_sent(notification_id, canal_message_id)

    async def _handle_outcome_unknown(self, notification_id: int) -> None:
        # A claim without a channel message id aged past the TTL: a crash left the
        # send outcome unknown. Report FALHA and alert so the ambiguity is
        # surfaced instead of risking a duplicate delivery with a resend.
        logger.warning("delivery_outcome_unknown", extra={"notificacao": notification_id})
        await self._confirm(
            notification_id,
            ConfirmacaoEnvioRequest(statusEnvio=StatusEnvioNotificacao.FALHA),
        )
        if self._alerts is not None:
            await self._alerts.raise_alert(
                "Notificação com status de envio desconhecido após falha do "
                f"serviço (id {notification_id})."
            )

    async def _handle_window_closed(self, notification_id: int) -> None:
        # The window passed while the service was down and the notification was
        # never sent: report FALHA and raise an operational alert (late
        # delivery, window closed).
        logger.warning("delivery_window_closed", extra={"notificacao": notification_id})
        await self._confirm(
            notification_id,
            ConfirmacaoEnvioRequest(statusEnvio=StatusEnvioNotificacao.FALHA),
        )
        if self._alerts is not None:
            await self._alerts.raise_alert(
                "Notificação não enviada: janela encerrada antes do envio "
                f"(id {notification_id})."
            )

    async def _confirm_sent(self, notification_id: int, canal_message_id: str) -> None:
        await self._confirm(
            notification_id,
            ConfirmacaoEnvioRequest(
                statusEnvio=StatusEnvioNotificacao.ENVIADA,
                canalMessageId=canal_message_id,
            ),
        )

    async def _confirm(
        self, notification_id: int, request: ConfirmacaoEnvioRequest
    ) -> None:
        """Confirm the outcome with exponential backoff. When the core stays
        unavailable, give up for this poll; the sent guard keeps the state so the
        next poll confirms without resending."""
        delay = self._settings.confirm_base_s
        for attempt in range(1, self._settings.confirm_max_tentativas + 1):
            try:
                await self._core.confirmar_envio(notification_id, request)
                return
            except CoreError as exc:
                if not exc.transient or attempt == self._settings.confirm_max_tentativas:
                    logger.warning(
                        "delivery_confirm_giving_up",
                        extra={"notificacao": notification_id, "tentativa": attempt},
                    )
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._settings.confirm_cap_s)


def _as_utc(value: datetime) -> datetime:
    """Return a tz-aware UTC datetime. The core's agenda window is a naive
    LocalDateTime in UTC (serverTimezone=UTC), so a naive value is stamped as
    UTC; an already-aware value is converted to UTC. This keeps every datetime
    comparison in the worker between aware instants."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_parametros(parametros: str | None) -> dict[str, object]:
    if not parametros:
        return {}
    loaded = json.loads(parametros)
    if not isinstance(loaded, dict):
        return {}
    return loaded


def create_delivery_worker(
    *,
    core: CoreClient,
    channel: ChannelPort,
    redis: object,
    recipient: str,
    settings: DeliverySettings | None = None,
    clock: Clock | None = None,
    random_source: RandomSource | None = None,
    alerts: AlertRaiser | None = None,
) -> DeliveryWorker:
    """Factory with explicit dependencies, so `main` wires the worker without the
    delivery package importing application wiring."""
    return DeliveryWorker(
        core=core,
        channel=channel,
        state=DeliveryState(redis),
        recipient=recipient,
        settings=settings or DeliverySettings.from_env(),
        clock=clock or SystemClock(),
        random_source=random_source or SystemRandom(),
        alerts=alerts,
    )
