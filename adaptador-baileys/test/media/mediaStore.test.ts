import assert from 'node:assert/strict'
import { test } from 'node:test'
import { MediaRefStore } from '../../src/media/mediaStore.js'

test('issues an opaque ref that resolves to the message id', () => {
  const store = new MediaRefStore(1000, () => 0)
  const ref = store.issue('msg-1')
  assert.notEqual(ref, 'msg-1')
  assert.equal(store.resolve(ref), 'msg-1')
})

test('expires refs after the ttl', () => {
  let now = 0
  const store = new MediaRefStore(1000, () => now)
  const ref = store.issue('msg-1')
  now = 1000
  assert.equal(store.resolve(ref), null)
})

test('unknown refs resolve to null', () => {
  const store = new MediaRefStore(1000, () => 0)
  assert.equal(store.resolve('nope'), null)
})

test('prune removes expired entries', () => {
  let now = 0
  const store = new MediaRefStore(1000, () => now)
  const ref = store.issue('msg-1')
  now = 2000
  store.prune()
  assert.equal(store.resolve(ref), null)
})
