import assert from 'node:assert/strict'
import { test } from 'node:test'
import { SentMessageRegistry } from '../../src/whatsapp/sentRegistry.js'

// A controllable clock so expiry is driven by the test, never by sleeping.
function fakeClock(start = 0) {
  let now = start
  return {
    now: () => now,
    advance: (ms: number) => {
      now += ms
    },
  }
}

test('recognizes an id it just recorded', () => {
  const registry = new SentMessageRegistry(() => 0)
  registry.add('m1')
  assert.equal(registry.has('m1'), true)
  assert.equal(registry.has('other'), false)
})

test('forgets an id once its TTL has elapsed', () => {
  const clock = fakeClock()
  const ttl = 1000
  const registry = new SentMessageRegistry(clock.now, 500, ttl)
  registry.add('m1')
  clock.advance(ttl)
  // At exactly the TTL boundary the entry is considered expired.
  assert.equal(registry.has('m1'), false)
})

test('keeps an id that is still within its TTL', () => {
  const clock = fakeClock()
  const registry = new SentMessageRegistry(clock.now, 500, 1000)
  registry.add('m1')
  clock.advance(999)
  assert.equal(registry.has('m1'), true)
})

test('evicts the oldest ids once capacity is exceeded', () => {
  const clock = fakeClock()
  const registry = new SentMessageRegistry(clock.now, 2, 60000)
  registry.add('a')
  clock.advance(1)
  registry.add('b')
  clock.advance(1)
  registry.add('c')
  // Capacity is 2, so the oldest ('a') is dropped and the two newest remain.
  assert.equal(registry.has('a'), false)
  assert.equal(registry.has('b'), true)
  assert.equal(registry.has('c'), true)
})

test('re-adding an id refreshes its position so it is evicted last', () => {
  const clock = fakeClock()
  const registry = new SentMessageRegistry(clock.now, 2, 60000)
  registry.add('a')
  clock.advance(1)
  registry.add('b')
  clock.advance(1)
  registry.add('a')
  clock.advance(1)
  registry.add('c')
  // 'a' was refreshed, so 'b' is now the oldest and the one evicted.
  assert.equal(registry.has('b'), false)
  assert.equal(registry.has('a'), true)
  assert.equal(registry.has('c'), true)
})
