import { loadConfig, type AdapterConfig } from './config/config.js'
import { logger } from './config/logger.js'
import { buildServer } from './http/server.js'
import { MediaRefStore } from './media/mediaStore.js'
import { filterInbound } from './whatsapp/allowlist.js'
import { BaileysWaSocket } from './whatsapp/baileysSocket.js'
import { DisabledWaSocket } from './whatsapp/disabledSocket.js'
import type { RawInbound, SessionEvent, WaSocket } from './whatsapp/types.js'
import { WebhookForwarder } from './webhook/webhook.js'

async function main(): Promise<void> {
  const config: AdapterConfig = loadConfig()
  const media = new MediaRefStore(config.mediaTtlMs)
  let socket: WaSocket

  if (config.baileysEnabled) {
    const forwarder = new WebhookForwarder(config.webhookUrl, config.webhookSecret)

    const onInbound = async (raw: RawInbound): Promise<void> => {
      const result = filterInbound(raw, config.grupoJid)
      if (!result.allowed) {
        // Dropped before any content touches a log or the webhook.
        logger.info({ reason: result.reason }, 'inbound dropped')
        return
      }
      const { needsMedia, ...message } = result.message
      const mediaRef = needsMedia ? media.issue(message.id) : null
      await forwarder.forward({ event: 'message', ...message, mediaRef }).catch((err) => {
        logger.error({ err, messageId: message.id }, 'failed to forward message')
      })
    }

    const onSession = async (event: SessionEvent): Promise<void> => {
      logger.info({ type: event.type }, 'session event')
      await forwarder.forward({ event: 'session', ...event }).catch((err) => {
        logger.error({ err, type: event.type }, 'failed to forward session event')
      })
    }

    if (config.discoverGroups) {
      logger.warn('DISCOVER_GROUPS=true: group ids and names will be logged; turn it off after copying GRUPO_JID')
    }
    const baileys = new BaileysWaSocket(
      config.sessionDir,
      onInbound,
      onSession,
      config.mediaMaxBytes,
      config.discoverGroups,
    )
    await baileys.start()
    socket = baileys
  } else {
    logger.warn('BAILEYS_ENABLED=false; starting without a WhatsApp connection')
    socket = new DisabledWaSocket()
  }

  const app = buildServer({
    socket,
    media,
    adapterSecret: config.adapterSecret,
    sendAllowedJid: config.grupoJid,
  })

  await app.listen({ host: config.host, port: config.port })
  logger.info({ port: config.port }, 'adapter listening')
}

main().catch((err) => {
  logger.error({ err }, 'fatal startup error')
  process.exit(1)
})
