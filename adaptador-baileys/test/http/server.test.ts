import assert from 'node:assert/strict'
import { test } from 'node:test'
import { FakeWaSocket } from '../support/fakeSocket.js'
import { MediaRefStore } from '../../src/media/mediaStore.js'
import { buildServer } from '../../src/http/server.js'
import { MediaRejectedError } from '../../src/whatsapp/types.js'

const SECRET = 'adapter-secret'
const GROUP = 'g@g.us'

function makeApp() {
  const socket = new FakeWaSocket()
  const media = new MediaRefStore(60000)
  const app = buildServer({ socket, media, adapterSecret: SECRET, sendAllowedJid: GROUP })
  return { app, socket, media }
}

test('GET /health returns the connection state and no secrets', async () => {
  const { app, socket } = makeApp()
  socket.state = 'connected'
  const res = await app.inject({ method: 'GET', url: '/health' })
  assert.equal(res.statusCode, 200)
  const body = res.json()
  assert.deepEqual(body, { status: 'connected' })
  assert.equal(res.payload.includes(SECRET), false)
  await app.close()
})

test('POST /messages requires the shared secret', async () => {
  const { app } = makeApp()
  const res = await app.inject({ method: 'POST', url: '/messages', payload: { text: 'hi' } })
  assert.equal(res.statusCode, 401)
  await app.close()
})

test('POST /messages sends to the dedicated group only', async () => {
  const { app, socket } = makeApp()
  const res = await app.inject({
    method: 'POST',
    url: '/messages',
    headers: { 'x-adapter-secret': SECRET },
    payload: { text: 'hi', to: 'someone-else@s.whatsapp.net' },
  })
  assert.equal(res.statusCode, 202)
  assert.deepEqual(socket.sent, [{ to: GROUP, text: 'hi' }])
  await app.close()
})

test('POST /messages rejects an empty body', async () => {
  const { app } = makeApp()
  const res = await app.inject({
    method: 'POST',
    url: '/messages',
    headers: { 'x-adapter-secret': SECRET },
    payload: {},
  })
  assert.equal(res.statusCode, 400)
  await app.close()
})

test('GET /media/:ref streams media for a valid ref and 404s for unknown', async () => {
  const { app, media } = makeApp()
  const ref = media.issue('msg-1')

  const ok = await app.inject({
    method: 'GET',
    url: `/media/${ref}`,
    headers: { 'x-adapter-secret': SECRET },
  })
  assert.equal(ok.statusCode, 200)
  assert.equal(ok.headers['content-type'], 'image/jpeg')
  assert.equal(ok.rawPayload.toString(), 'fake')

  const missing = await app.inject({
    method: 'GET',
    url: '/media/unknown-ref',
    headers: { 'x-adapter-secret': SECRET },
  })
  assert.equal(missing.statusCode, 404)
  await app.close()
})

test('GET /media/:ref requires the shared secret', async () => {
  const { app, media } = makeApp()
  const ref = media.issue('msg-1')
  const res = await app.inject({ method: 'GET', url: `/media/${ref}` })
  assert.equal(res.statusCode, 401)
  await app.close()
})

test('GET /media/:ref returns 413 when media exceeds the size cap', async () => {
  const { app, socket, media } = makeApp()
  socket.downloadError = new MediaRejectedError('media exceeds the configured size cap', 413)
  const ref = media.issue('msg-1')
  const res = await app.inject({
    method: 'GET',
    url: `/media/${ref}`,
    headers: { 'x-adapter-secret': SECRET },
  })
  assert.equal(res.statusCode, 413)
  await app.close()
})

test('GET /media/:ref returns 415 for a disallowed media type', async () => {
  const { app, socket, media } = makeApp()
  socket.downloadError = new MediaRejectedError('unsupported media type: application/pdf', 415)
  const ref = media.issue('msg-1')
  const res = await app.inject({
    method: 'GET',
    url: `/media/${ref}`,
    headers: { 'x-adapter-secret': SECRET },
  })
  assert.equal(res.statusCode, 415)
  await app.close()
})
