// Connection health states reported by GET /health.
export type HealthState = 'connected' | 'degraded' | 'down'

export type MessageKind = 'text' | 'image' | 'unsupported'

// Normalized inbound message forwarded to the orchestrator webhook.
export interface NormalizedMessage {
  id: string
  groupJid: string
  senderJid: string
  fromMe: boolean
  kind: MessageKind
  text: string
  mediaRef: string | null
  timestamp: number
}

export interface SessionEvent {
  type: 'connected' | 'disconnected' | 'logged_out'
  timestamp: number
}

export type AdapterEvent =
  | ({ event: 'message' } & NormalizedMessage)
  | ({ event: 'session' } & SessionEvent)

// Raw inbound as produced by the socket layer, before allowlist filtering.
// For a group message `remoteJid` is the group id, so the author lives in the
// message key instead: `participantJid` is Baileys `key.participant` (a phone
// JID on legacy accounts, a `@lid` on newer ones) and `participantPn` is the
// phone-number alternative (`key.participantPn`) when WhatsApp exposes it.
export interface RawInbound {
  id: string
  remoteJid: string | null | undefined
  participantJid: string | null | undefined
  participantPn: string | null | undefined
  fromMe: boolean
  kind: MessageKind
  text: string
  hasMedia: boolean
  timestamp: number
}

export interface OutgoingText {
  to: string
  text: string
}

// Media downloaded on demand and streamed back through GET /media/{ref}.
export interface MediaPayload {
  data: Buffer
  mimetype: string
}

// Raised when media is refused before it is fully buffered: either its size
// exceeds the configured cap (413) or its type is not an allowed image (415).
// The HTTP layer maps `status` directly onto the response code.
export class MediaRejectedError extends Error {
  constructor(
    message: string,
    readonly status: 413 | 415,
  ) {
    super(message)
    this.name = 'MediaRejectedError'
  }
}

// The WhatsApp socket is hidden behind this interface so the adapter logic
// (allowlist, signing, media, health, reconnect) is testable with a fake.
export interface WaSocket {
  sendText(to: string, text: string): Promise<void>
  downloadMedia(messageId: string): Promise<MediaPayload>
  health(): HealthState
  close(): Promise<void>
  // True when the id belongs to a message the adapter itself sent. Used only by
  // the opt-in own-message test mode to drop the adapter's own echoes.
  isSelfSent(id: string): boolean
  // The paired account's own JID, or null when unpaired. Used to attribute own
  // messages (which carry no participant) in the test mode.
  ownJid(): string | null
}

export type InboundHandler = (raw: RawInbound) => void | Promise<void>
export type SessionHandler = (event: SessionEvent) => void | Promise<void>
