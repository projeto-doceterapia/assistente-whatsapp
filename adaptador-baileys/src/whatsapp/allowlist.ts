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

// Options enabling the opt-in single-number test mode. Kept as a pure argument
// so the filter stays side-effect free: `isSelfSent` only reads the registry,
// `ownJid` is the paired account's JID used to attribute own messages.
export interface FilterOptions {
  acceptOwnMessages: boolean
  isSelfSent: (id: string) => boolean
  ownJid: string | null | undefined
}

const DEFAULT_OPTIONS: FilterOptions = {
  acceptOwnMessages: false,
  isSelfSent: () => false,
  ownJid: null,
}

// Enforces the dedicated-group allowlist and own-message filtering, and
// normalizes the shape. A dropped message never carries its content forward,
// so callers can log the reason without ever logging the body.
export function filterInbound(
  raw: RawInbound,
  grupoJid: string,
  options: FilterOptions = DEFAULT_OPTIONS,
): FilterResult {
  if (raw.remoteJid !== grupoJid) {
    return { allowed: false, reason: 'not_allowed_chat' }
  }
  if (raw.fromMe) {
    // Own messages are dropped unless the test mode is on. Even then, an echo of
    // a message the adapter itself sent is still dropped as `own_message`,
    // otherwise the assistant would answer its own replies in a loop.
    if (!options.acceptOwnMessages || options.isSelfSent(raw.id)) {
      return { allowed: false, reason: 'own_message' }
    }
  }
  if (raw.kind === 'text' && raw.text.trim() === '') {
    return { allowed: false, reason: 'empty' }
  }

  // The chat jid is the group id, so the author is resolved from the key. Own
  // group messages carry no participant, so we attribute them to the paired
  // account's own JID, letting resolveSender yield a phone sender.
  const resolvable =
    raw.fromMe && !raw.participantJid && !raw.participantPn && options.ownJid
      ? { ...raw, participantJid: options.ownJid }
      : raw
  const sender = resolveSender(resolvable)
  return {
    allowed: true,
    message: {
      id: raw.id,
      groupJid: grupoJid,
      senderJid: sender.jid,
      // The orchestrator discards fromMe:true events, so an accepted own
      // message is forwarded as fromMe:false to be processed normally.
      fromMe: false,
      kind: raw.kind,
      text: raw.text,
      timestamp: raw.timestamp,
      needsMedia: raw.hasMedia && raw.kind === 'image',
    },
    senderKind: sender.kind,
  }
}
