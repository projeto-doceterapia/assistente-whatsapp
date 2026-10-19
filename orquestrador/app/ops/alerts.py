"""Operational alerts.

An operational problem is surfaced two ways at once:
  1. always as an in-app `ALERTA_OPERACIONAL` notification through the core
     (`POST /notificacoes`), so the owner sees it in the web app even when
     WhatsApp is the failing part;
  2. also as a WhatsApp message to the owner, but only when the channel reports
     healthy. When the channel itself is down, no WhatsApp send is attempted.

Every alert carries a dedup/cool-down key in Redis, so a persistent failure
(polled every minute) does not produce an alert storm: the same key is silent
until its cool-down expires (default 30 min). Alert texts never contain receipt
data or secrets; they state the condition only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.channel.port import ChannelPort, HealthStatus
from app.core.client import CoreClient
from app.core.errors import CoreError
from app.core.models import NotificacaoRequest, TipoNotificacao
from app.ops.settings import OpsSettings

logger = logging.getLogger("ops.alerts")

_TITULO = "Alerta operacional"


class AlertKind(str, Enum):
    """Operational conditions the orchestrator alerts on.

    The value is both the stable dedup key suffix and the log code. Conditions
    tied to a specific subject (a DLQ item, a missed notification) append that
    subject to the key so distinct subjects alert independently while the same
    subject stays deduped.
    """

    SESSAO_WHATSAPP = "sessao_whatsapp"
    ITEM_DLQ = "item_dlq"
    CORE_FORA = "core_fora"
    NOTIFICACAO_NAO_ENVIADA = "notificacao_nao_enviada"
    ERROS_IA = "erros_ia"
    REDIS_FORA = "redis_fora"


# PT-BR messages shown to the owner. They describe the condition only: no
# receipt content, no ids that leak data, no secrets.
_MENSAGENS: dict[AlertKind, str] = {
    AlertKind.SESSAO_WHATSAPP: (
        "A sessão do WhatsApp do assistente caiu. As mensagens podem não ser "
        "entregues até a reconexão."
    ),
    AlertKind.ITEM_DLQ: (
        "Um registro de compra não pôde ser concluído automaticamente e foi "
        "separado para revisão manual."
    ),
    AlertKind.CORE_FORA: (
        "O sistema de estoque está fora do ar para o assistente. As operações "
        "serão retomadas automaticamente quando ele voltar."
    ),
    AlertKind.NOTIFICACAO_NAO_ENVIADA: (
        "Uma notificação não foi entregue dentro da janela prevista."
    ),
    AlertKind.ERROS_IA: (
        "A leitura automática de compras está falhando repetidamente. Pode ser "
        "necessário registrar manualmente por enquanto."
    ),
    AlertKind.REDIS_FORA: (
        "O armazenamento temporário do assistente está indisponível. As "
        "conversas em andamento podem ser interrompidas."
    ),
}


@dataclass(frozen=True)
class _Alert:
    kind: AlertKind
    mensagem: str
    dedup_suffix: str | None = None

    def dedup_key(self) -> str:
        suffix = f":{self.dedup_suffix}" if self.dedup_suffix else ""
        return f"ops:alert:{self.kind.value}{suffix}"


class OperationalAlerts:
    """Raises operational alerts with cool-down and a healthy-channel WhatsApp
    copy. It implements the delivery `AlertRaiser` port (`raise_alert`) so the
    delivery worker reports a missed notification through the same path."""

    def __init__(
        self,
        *,
        core: CoreClient,
        channel: ChannelPort,
        redis: object,
        recipient: str,
        settings: OpsSettings | None = None,
    ) -> None:
        self._core = core
        self._channel = channel
        self._redis = redis
        self._recipient = recipient
        self._settings = settings or OpsSettings()

    # --- public alert entry points ----------------------------------------

    async def raise_alert(self, mensagem: str) -> None:
        """AlertRaiser port: a missed notification reported by the delivery
        worker. The worker already builds a PT-BR message, but it may carry a
        notification id; the stored alert uses the fixed, id-free message while
        the delivery worker's own text is only logged."""
        logger.warning("ops_alert_missed_notification", extra={"detail": mensagem})
        await self._raise(
            _Alert(
                AlertKind.NOTIFICACAO_NAO_ENVIADA,
                _MENSAGENS[AlertKind.NOTIFICACAO_NAO_ENVIADA],
            )
        )

    async def raise_session_down(self) -> None:
        await self._raise(
            _Alert(AlertKind.SESSAO_WHATSAPP, _MENSAGENS[AlertKind.SESSAO_WHATSAPP])
        )

    async def raise_dlq_item(self, item: object) -> None:
        """Called by the retry worker when it moves an item to the DLQ. The item
        is only used to derive a stable dedup suffix; its content is never put in
        the alert text."""
        suffix = _dlq_suffix(item)
        await self._raise(
            _Alert(AlertKind.ITEM_DLQ, _MENSAGENS[AlertKind.ITEM_DLQ], suffix)
        )

    async def raise_core_down(self) -> None:
        await self._raise(_Alert(AlertKind.CORE_FORA, _MENSAGENS[AlertKind.CORE_FORA]))

    async def raise_ai_errors(self) -> None:
        await self._raise(_Alert(AlertKind.ERROS_IA, _MENSAGENS[AlertKind.ERROS_IA]))

    async def raise_redis_down(self) -> None:
        await self._raise(
            _Alert(AlertKind.REDIS_FORA, _MENSAGENS[AlertKind.REDIS_FORA])
        )

    # --- internals ---------------------------------------------------------

    async def _raise(self, alert: _Alert) -> None:
        if not await self._claim_cooldown(alert):
            return  # still inside the cool-down window: stay silent
        await self._create_in_app(alert)
        await self._try_whatsapp(alert)

    async def _claim_cooldown(self, alert: _Alert) -> bool:
        """Reserve the cool-down slot for this alert key. Returns True only for
        the first caller inside the window (NX). When Redis itself is down this
        must not swallow the alert: on error it reports True so the alert still
        goes out (a Redis-down alert cannot depend on Redis)."""
        try:
            acquired = await self._redis.set(
                alert.dedup_key(),
                "1",
                nx=True,
                ex=self._settings.alert_cooldown_s,
            )
        except Exception:
            logger.exception("ops_cooldown_unavailable", extra={"kind": alert.kind.value})
            return True
        return bool(acquired)

    async def _create_in_app(self, alert: _Alert) -> None:
        try:
            await self._core.criar_notificacao(
                NotificacaoRequest(
                    tipo=TipoNotificacao.ALERTA_OPERACIONAL,
                    titulo=_TITULO,
                    mensagem=alert.mensagem,
                )
            )
        except CoreError:
            # The core is the alert sink; if it is down the alert cannot be
            # recorded now. Log and continue so a WhatsApp copy may still go out.
            logger.warning("ops_alert_in_app_failed", extra={"kind": alert.kind.value})

    async def _try_whatsapp(self, alert: _Alert) -> None:
        try:
            health = await self._channel.health()
        except Exception:
            logger.exception("ops_alert_health_check_failed", extra={"kind": alert.kind.value})
            return
        if health is not HealthStatus.CONNECTED:
            # Channel not healthy: in-app alert already created, no send attempt.
            return
        try:
            await self._channel.send_text(self._recipient, f"⚠️ {alert.mensagem}")
        except Exception:
            logger.exception("ops_alert_whatsapp_failed", extra={"kind": alert.kind.value})


def _dlq_suffix(item: object) -> str | None:
    """Derive a stable, non-sensitive dedup suffix from a DLQ item. Prefers an
    idempotency key or an index; falls back to no suffix (one DLQ alert per
    cool-down) rather than embedding item content."""
    for attr in ("idempotency_key", "index"):
        value = getattr(item, attr, None)
        if value is not None:
            return str(value)
    if isinstance(item, dict):
        for attr in ("idempotency_key", "index"):
            if item.get(attr) is not None:
                return str(item[attr])
    return None
