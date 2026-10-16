import assert from 'node:assert/strict'
import { test } from 'node:test'
import { decideReconnect } from '../../src/whatsapp/reconnect.js'

const OPTS = { baseMs: 1000, maxMs: 60000 }

test('does not reconnect after an explicit logout', () => {
  const decision = decideReconnect(true, 0, OPTS)
  assert.equal(decision.reconnect, false)
  assert.equal(decision.delayMs, 0)
})

test('reconnects after a network drop', () => {
  const decision = decideReconnect(false, 0, OPTS, () => 1)
  assert.equal(decision.reconnect, true)
})

test('backoff grows exponentially and is bounded by maxMs', () => {
  // random() = 1 yields the full (unjittered) window minus one ms via floor.
  const first = decideReconnect(false, 0, OPTS, () => 1).delayMs
  const second = decideReconnect(false, 1, OPTS, () => 1).delayMs
  const huge = decideReconnect(false, 20, OPTS, () => 1).delayMs
  assert.ok(second > first)
  assert.ok(huge <= OPTS.maxMs)
})

test('applies jitter below the exponential window', () => {
  const delay = decideReconnect(false, 3, OPTS, () => 0.5).delayMs
  assert.ok(delay >= 0 && delay <= OPTS.baseMs * 2 ** 3)
})
