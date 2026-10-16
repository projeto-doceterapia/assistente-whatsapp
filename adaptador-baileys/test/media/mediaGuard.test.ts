import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  assertAllowedMimetype,
  assertDeclaredSizeWithinCap,
  readCapped,
} from '../../src/media/mediaGuard.js'
import { MediaRejectedError } from '../../src/whatsapp/types.js'

async function* chunks(parts: Buffer[]): AsyncGenerator<Buffer> {
  for (const part of parts) {
    yield part
  }
}

test('accepts allowed image mimetypes', () => {
  for (const mimetype of ['image/jpeg', 'image/png', 'image/webp']) {
    assert.doesNotThrow(() => assertAllowedMimetype(mimetype))
  }
})

test('rejects a disallowed mimetype with 415', () => {
  try {
    assertAllowedMimetype('application/pdf')
    assert.fail('expected rejection')
  } catch (err) {
    assert.ok(err instanceof MediaRejectedError)
    assert.equal(err.status, 415)
  }
})

test('rejects a declared size above the cap with 413', () => {
  try {
    assertDeclaredSizeWithinCap(100, 50)
    assert.fail('expected rejection')
  } catch (err) {
    assert.ok(err instanceof MediaRejectedError)
    assert.equal(err.status, 413)
  }
})

test('allows a declared size at or below the cap', () => {
  assert.doesNotThrow(() => assertDeclaredSizeWithinCap(50, 50))
  assert.doesNotThrow(() => assertDeclaredSizeWithinCap(undefined, 50))
})

test('readCapped returns the full buffer when under the cap', async () => {
  const data = await readCapped(chunks([Buffer.from('ab'), Buffer.from('cd')]), 10)
  assert.equal(data.toString(), 'abcd')
})

test('readCapped aborts with 413 when the stream exceeds the cap', async () => {
  // Declared size may be absent or lie, so the running total must be enforced.
  await assert.rejects(
    () => readCapped(chunks([Buffer.from('aaaa'), Buffer.from('bbbb')]), 5),
    (err: unknown) => err instanceof MediaRejectedError && err.status === 413,
  )
})
