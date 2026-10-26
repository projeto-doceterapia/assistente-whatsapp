import assert from 'node:assert/strict'
import { test } from 'node:test'
import { resolveSender } from '../../src/whatsapp/sender.js'
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

test('uses a phone participant as the sender', () => {
  const result = resolveSender(raw({ participantJid: '5511999@s.whatsapp.net' }))
  assert.deepEqual(result, { jid: '5511999@s.whatsapp.net', kind: 'phone' })
})

test('prefers the phone alternative over a LID participant', () => {
  const result = resolveSender(
    raw({ participantJid: 'abc@lid', participantPn: '5511999@s.whatsapp.net' }),
  )
  assert.deepEqual(result, { jid: '5511999@s.whatsapp.net', kind: 'phone' })
})

test('falls back to the LID participant when no phone jid is known', () => {
  const result = resolveSender(raw({ participantJid: 'abc@lid' }))
  assert.deepEqual(result, { jid: 'abc@lid', kind: 'lid' })
})

test('falls back to the chat jid with unknown when no author is known', () => {
  const result = resolveSender(raw())
  assert.deepEqual(result, { jid: GROUP, kind: 'unknown' })
})

test('preserves the device suffix on a phone jid', () => {
  const result = resolveSender(raw({ participantJid: '5511999:12@s.whatsapp.net' }))
  assert.deepEqual(result, { jid: '5511999:12@s.whatsapp.net', kind: 'phone' })
})
