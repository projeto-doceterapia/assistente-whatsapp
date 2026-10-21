import assert from 'node:assert/strict'
import { test } from 'node:test'
import { signBody, verifySignature } from '../../src/webhook/hmac.js'

const SECRET = 'shared-secret'

test('signBody produces a stable sha256-prefixed signature', () => {
  const sig = signBody(SECRET, '{"a":1}')
  assert.match(sig, /^sha256=[0-9a-f]{64}$/)
  assert.equal(sig, signBody(SECRET, '{"a":1}'))
})

test('verifySignature accepts a correct signature', () => {
  const body = '{"event":"message"}'
  assert.equal(verifySignature(SECRET, body, signBody(SECRET, body)), true)
})

test('verifySignature rejects a wrong signature', () => {
  const body = '{"event":"message"}'
  assert.equal(verifySignature(SECRET, body, signBody('other', body)), false)
})

test('verifySignature rejects a missing signature', () => {
  assert.equal(verifySignature(SECRET, 'x', undefined), false)
})

test('verifySignature rejects a tampered body', () => {
  const sig = signBody(SECRET, 'original')
  assert.equal(verifySignature(SECRET, 'tampered', sig), false)
})
