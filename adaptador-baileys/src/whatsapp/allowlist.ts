import type { NormalizedMessage, RawInbound } from './types.js'
import { resolveSender, type SenderKind } from './sender.js'

export type DropReason = 'not_allowed_chat' | 'own_message' | 'empty'

export type FilterResult =
  | {
      allowed: true
      message: Omit<NormalizedMessage, 'mediaRef'> & { needsMedia: boolean }
      senderKind: SenderKind
    }
  | { allowed: false; reason: DropReason }

// Enforces the dedicated-group allowlist and own-message filtering, and
// normalizes the shape. A dropped message never carries its content forward,
// so callers can log the reason without ever logging the body.
export function filterInbound(raw: RawInbound, grupoJid: string): FilterResult {
  if (raw.remoteJid !== grupoJid) {
    return { allowed: false, reason: 'not_allowed_chat' }
  }
  if (raw.fromMe) {
    return { allowed: false, reason: 'own_message' }
  }
  if (raw.kind === 'text' && raw.text.trim() === '') {
    return { allowed: false, reason: 'empty' }
  }

  // The chat jid is the group id, so the author is resolved from the key. Its
  // kind is returned so the caller can log which sender form real accounts use.
  const sender = resolveSender(raw)
  return {
    allowed: true,
    message: {
      id: raw.id,
      groupJid: grupoJid,
      senderJid: sender.jid,
      fromMe: false,
      kind: raw.kind,
      text: raw.text,
      timestamp: raw.timestamp,
      needsMedia: raw.hasMedia && raw.kind === 'image',
    },
    senderKind: sender.kind,
  }
}
