import type { RawInbound } from './types.js'

export type SenderKind = 'phone' | 'lid' | 'unknown'

export interface ResolvedSender {
  jid: string
  kind: SenderKind
}

// WhatsApp phone JIDs live on this domain; the orchestrator keys beneficiaries
// by phone number, so a phone JID is always preferred over a LID.
const PHONE_DOMAIN = 's.whatsapp.net'
const LID_DOMAIN = 'lid'

// Returns the domain part of a JID (everything after the last '@'), keeping any
// device suffix on the local part untouched (`5511...:12@s.whatsapp.net`).
function domainOf(jid: string): string {
  const at = jid.lastIndexOf('@')
  return at === -1 ? '' : jid.slice(at + 1)
}

function isNonEmpty(value: string | null | undefined): value is string {
  return typeof value === 'string' && value !== ''
}

// Resolves the real author of a (possibly group) message. For a group message
// the chat `remoteJid` is the group id, so the author must come from the key:
// `participantJid` is `key.participant` and `participantPn` its phone-number
// alternative. A phone JID wins over a LID because the orchestrator matches the
// beneficiary by digits; when no author is known we fall back to the chat jid
// with kind 'unknown', which the orchestrator then safely ignores.
export function resolveSender(raw: RawInbound): ResolvedSender {
  const candidates = [raw.participantJid, raw.participantPn].filter(isNonEmpty)

  const phone = candidates.find((jid) => domainOf(jid) === PHONE_DOMAIN)
  if (phone !== undefined) {
    return { jid: phone, kind: 'phone' }
  }

  const lid = candidates.find((jid) => domainOf(jid) === LID_DOMAIN)
  if (lid !== undefined) {
    return { jid: lid, kind: 'lid' }
  }

  return { jid: raw.remoteJid ?? '', kind: 'unknown' }
}
