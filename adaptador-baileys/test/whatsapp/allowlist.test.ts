import assert from 'node:assert/strict'
import { test } from 'node:test'
import { filterInbound } from '../../src/whatsapp/allowlist.js'
import type { RawInbound } from '../../src/whatsapp/types.js'

const GROUP = '123@g.us'

function raw(overrides: Partial<RawInbound> = {}): RawInbound {
  return {
    id: 'm1',
    remoteJid: GROUP,
    participantJid: null,
    participantPn: null,
    fromMe: false,
    kind: 'text',
    text: 'oi',
    hasMedia: false,
    timestamp: 1000,
    ...overrides,
  }
}

test('accepts a text message from the allowed group', () => {
  const result = filterInbound(raw(), GROUP)
  assert.equal(result.allowed, true)
  if (result.allowed) {
    assert.equal(result.message.groupJid, GROUP)
    assert.equal(result.message.kind, 'text')
    assert.equal(result.message.needsMedia, false)
  }
})

test('drops a message from a non-allowed chat without content', () => {
  const result = filterInbound(raw({ remoteJid: '999@s.whatsapp.net', text: 'secret' }), GROUP)
  assert.equal(result.allowed, false)
  if (!result.allowed) {
    assert.equal(result.reason, 'not_allowed_chat')
    // A dropped result carries only a reason, never the message content.
    assert.deepEqual(Object.keys(result).sort(), ['allowed', 'reason'])
  }
})

test('drops own messages', () => {
  const result = filterInbound(raw({ fromMe: true }), GROUP)
  assert.equal(result.allowed, false)
  if (!result.allowed) {
    assert.equal(result.reason, 'own_message')
  }
})

const OWN_JID = '5511888:12@s.whatsapp.net'

function ownMode(overrides: Partial<Parameters<typeof filterInbound>[2]> = {}) {
  return {
    acceptOwnMessages: true,
    isSelfSent: () => false,
    ownJid: OWN_JID,
    ...overrides,
  }
}

test('drops own messages even with the flag off (default options)', () => {
  const result = filterInbound(raw({ fromMe: true }), GROUP, {
    acceptOwnMessages: false,
    isSelfSent: () => false,
    ownJid: OWN_JID,
  })
  assert.equal(result.allowed, false)
  if (!result.allowed) {
    assert.equal(result.reason, 'own_message')
  }
})

test('accepts an own message not in the registry when the flag is on', () => {
  const result = filterInbound(raw({ fromMe: true, id: 'typed-1' }), GROUP, ownMode())
  assert.equal(result.allowed, true)
  if (result.allowed) {
    // Attributed to the paired account's own JID, resolved as a phone sender,
    // and forwarded as fromMe:false so the orchestrator processes it.
    assert.equal(result.message.senderJid, OWN_JID)
    assert.equal(result.senderKind, 'phone')
    assert.equal(result.message.fromMe, false)
  }
})

test('drops the adapter own echo when the flag is on', () => {
  const result = filterInbound(raw({ fromMe: true, id: 'sent-1' }), GROUP, {
    acceptOwnMessages: true,
    isSelfSent: (id) => id === 'sent-1',
    ownJid: OWN_JID,
  })
  assert.equal(result.allowed, false)
  if (!result.allowed) {
    assert.equal(result.reason, 'own_message')
  }
})

test('keeps an own message unknown when the own JID is unavailable', () => {
  const result = filterInbound(raw({ fromMe: true }), GROUP, ownMode({ ownJid: null }))
  assert.equal(result.allowed, true)
  if (result.allowed) {
    assert.equal(result.senderKind, 'unknown')
  }
})

test('drops a non-allowed chat first even when the message is own and the flag is on', () => {
  const result = filterInbound(
    raw({ remoteJid: '999@s.whatsapp.net', fromMe: true }),
    GROUP,
    ownMode(),
  )
  assert.equal(result.allowed, false)
  if (!result.allowed) {
    assert.equal(result.reason, 'not_allowed_chat')
  }
})

test('drops empty text', () => {
  const result = filterInbound(raw({ text: '   ' }), GROUP)
  assert.equal(result.allowed, false)
  if (!result.allowed) {
    assert.equal(result.reason, 'empty')
  }
})

test('flags image messages as needing a media ref', () => {
  const result = filterInbound(raw({ kind: 'image', text: '', hasMedia: true }), GROUP)
  assert.equal(result.allowed, true)
  if (result.allowed) {
    assert.equal(result.message.needsMedia, true)
  }
})

test('uses the group participant as the sender, not the group id', () => {
  const result = filterInbound(raw({ participantJid: '5511999@s.whatsapp.net' }), GROUP)
  assert.equal(result.allowed, true)
  if (result.allowed) {
    assert.equal(result.message.groupJid, GROUP)
    assert.equal(result.message.senderJid, '5511999@s.whatsapp.net')
    assert.equal(result.senderKind, 'phone')
  }
})

test('falls back to the chat jid when a message carries no participant', () => {
  const result = filterInbound(raw(), GROUP)
  assert.equal(result.allowed, true)
  if (result.allowed) {
    assert.equal(result.message.senderJid, GROUP)
    assert.equal(result.senderKind, 'unknown')
  }
})

test('keeps the drop reasons and their order unchanged', () => {
  // not_allowed_chat wins over own_message wins over empty.
  const notAllowed = filterInbound(raw({ remoteJid: '999@s.whatsapp.net', fromMe: true, text: '  ' }), GROUP)
  assert.equal(notAllowed.allowed, false)
  if (!notAllowed.allowed) {
    assert.equal(notAllowed.reason, 'not_allowed_chat')
  }
  const own = filterInbound(raw({ fromMe: true, text: '  ' }), GROUP)
  assert.equal(own.allowed, false)
  if (!own.allowed) {
    assert.equal(own.reason, 'own_message')
  }
  const empty = filterInbound(raw({ text: '  ' }), GROUP)
  assert.equal(empty.allowed, false)
  if (!empty.allowed) {
    assert.equal(empty.reason, 'empty')
  }
})
