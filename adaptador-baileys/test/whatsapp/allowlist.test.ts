import assert from 'node:assert/strict'
import { test } from 'node:test'
import { filterInbound } from '../../src/whatsapp/allowlist.js'
import type { RawInbound } from '../../src/whatsapp/types.js'

const GROUP = '123@g.us'

function raw(overrides: Partial<RawInbound> = {}): RawInbound {
  return {
    id: 'm1',
    remoteJid: GROUP,
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
