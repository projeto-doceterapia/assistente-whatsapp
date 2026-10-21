import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  useMultiFileAuthState,
  type WAMessage,
  type WASocket as BaileysSocket,
} from '@whiskeysockets/baileys'
import { Boom } from '@hapi/boom'
import pino from 'pino'
import {
  assertAllowedMimetype,
  assertDeclaredSizeWithinCap,
  readCapped,
} from '../media/mediaGuard.js'
import { logger } from '../config/logger.js'
import { decideReconnect } from './reconnect.js'
import type {
  HealthState,
  InboundHandler,
  MediaPayload,
  MessageKind,
  RawInbound,
  SessionHandler,
  WaSocket,
} from './types.js'

const BACKOFF = { baseMs: 1000, maxMs: 60000 }

function classify(message: WAMessage): { kind: MessageKind; text: string; hasMedia: boolean } {
  const content = message.message
  if (content?.conversation !== undefined && content.conversation !== null) {
    return { kind: 'text', text: content.conversation, hasMedia: false }
  }
  if (content?.extendedTextMessage?.text) {
    return { kind: 'text', text: content.extendedTextMessage.text, hasMedia: false }
  }
  if (content?.imageMessage) {
    return { kind: 'image', text: content.imageMessage.caption ?? '', hasMedia: true }
  }
  return { kind: 'unsupported', text: '', hasMedia: false }
}

// Wraps the Baileys socket behind WaSocket. Keeps the message cache needed to
// download media on demand, disables history sync, and reconnects with backoff
// unless WhatsApp reports an explicit logout.
export class BaileysWaSocket implements WaSocket {
  private sock: BaileysSocket | null = null
  private state: HealthState = 'down'
  private attempt = 0
  private closed = false
  private readonly messages = new Map<string, WAMessage>()

  constructor(
    private readonly sessionDir: string,
    private readonly onInbound: InboundHandler,
    private readonly onSession: SessionHandler,
    private readonly mediaMaxBytes: number,
  ) {}

  async start(): Promise<void> {
    const { state, saveCreds } = await useMultiFileAuthState(this.sessionDir)
    const sock = makeWASocket({
      auth: state,
      logger: pino({ level: 'warn' }),
      syncFullHistory: false,
      shouldSyncHistoryMessage: () => false,
    })
    this.sock = sock

    sock.ev.on('creds.update', saveCreds)

    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update
      if (qr) {
        // Baileys only emits `qr` while the session is unpaired. The string is
        // logged so the operator can render it; it is not a secret and expires
        // in seconds, but logs must stay private since they identify the chat.
        logger.info({ qr }, 'scan QR to pair the WhatsApp session')
      }
      if (connection === 'open') {
        this.state = 'connected'
        this.attempt = 0
        void this.onSession({ type: 'connected', timestamp: Date.now() })
      } else if (connection === 'close') {
        this.handleClose(lastDisconnect?.error)
      } else if (connection === 'connecting') {
        this.state = 'degraded'
      }
    })

    sock.ev.on('messages.upsert', ({ messages, type }) => {
      if (type !== 'notify') {
        return
      }
      for (const message of messages) {
        this.messages.set(message.key.id ?? '', message)
        void this.onInbound(this.toRaw(message))
      }
    })
  }

  private toRaw(message: WAMessage): RawInbound {
    const { kind, text, hasMedia } = classify(message)
    return {
      id: message.key.id ?? '',
      remoteJid: message.key.remoteJid,
      fromMe: message.key.fromMe === true,
      kind,
      text,
      hasMedia,
      timestamp: Number(message.messageTimestamp ?? 0) * 1000,
    }
  }

  private handleClose(error: Error | undefined): void {
    const statusCode = (error as Boom | undefined)?.output?.statusCode
    const loggedOut = statusCode === DisconnectReason.loggedOut
    this.state = 'down'
    if (loggedOut) {
      void this.onSession({ type: 'logged_out', timestamp: Date.now() })
    } else {
      void this.onSession({ type: 'disconnected', timestamp: Date.now() })
    }
    if (this.closed) {
      return
    }
    const decision = decideReconnect(loggedOut, this.attempt, BACKOFF)
    if (!decision.reconnect) {
      logger.warn('session logged out; manual re-pairing required')
      return
    }
    this.attempt += 1
    setTimeout(() => {
      void this.start().catch((startError) => {
        logger.error({ err: startError }, 'reconnect attempt failed')
      })
    }, decision.delayMs)
  }

  async sendText(to: string, text: string): Promise<void> {
    if (this.sock === null) {
      throw new Error('socket not started')
    }
    await this.sock.sendMessage(to, { text })
  }

  async downloadMedia(messageId: string): Promise<MediaPayload> {
    const message = this.messages.get(messageId)
    if (message === undefined) {
      throw new Error('message not available for media download')
    }
    const imageMessage = message.message?.imageMessage
    const mimetype = imageMessage?.mimetype ?? 'application/octet-stream'
    assertAllowedMimetype(mimetype)
    assertDeclaredSizeWithinCap(imageMessage?.fileLength, this.mediaMaxBytes)
    const stream = (await downloadMediaMessage(message, 'stream', {})) as NodeJS.ReadableStream
    try {
      const data = await readCapped(stream as AsyncIterable<Buffer>, this.mediaMaxBytes)
      return { data, mimetype }
    } finally {
      ;(stream as { destroy?: () => void }).destroy?.()
    }
  }

  health(): HealthState {
    return this.state
  }

  async close(): Promise<void> {
    this.closed = true
    this.state = 'down'
    this.sock?.end(undefined)
  }
}
