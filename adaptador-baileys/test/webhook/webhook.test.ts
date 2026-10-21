import assert from 'node:assert/strict'
import { test } from 'node:test'
import { SIGNATURE_HEADER, verifySignature } from '../../src/webhook/hmac.js'
import type { AdapterEvent } from '../../src/whatsapp/types.js'
import { WebhookForwarder } from '../../src/webhook/webhook.js'

const SECRET = 'hook-secret'

function captureFetch() {
  const calls: Array<{ url: string; headers: Record<string, string>; body: string }> = []
  const impl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({
      url: String(url),
      headers: init?.headers as Record<string, string>,
      body: init?.body as string,
    })
    return new Response(null, { status: 200 })
  }) as typeof fetch
  return { calls, impl }
}

test('forwards a signed message event the orchestrator can verify', async () => {
  const { calls, impl } = captureFetch()
  const forwarder = new WebhookForwarder('http://orq/webhook', SECRET, impl)
  const event: AdapterEvent = {
    event: 'message',
    id: 'm1',
    groupJid: 'g@g.us',
    senderJid: 'g@g.us',
    fromMe: false,
    kind: 'text',
    text: 'oi',
    mediaRef: null,
    timestamp: 1,
  }

  await forwarder.forward(event)

  assert.equal(calls.length, 1)
  const call = calls[0]!
  const signature = call.headers[SIGNATURE_HEADER]
  assert.equal(verifySignature(SECRET, call.body, signature), true)
  assert.equal(verifySignature('wrong', call.body, signature), false)
})

test('throws when the webhook rejects the event', async () => {
  const impl = (async () => new Response(null, { status: 401 })) as typeof fetch
  const forwarder = new WebhookForwarder('http://orq/webhook', SECRET, impl)
  await assert.rejects(() =>
    forwarder.forward({ event: 'session', type: 'connected', timestamp: 1 }),
  )
})
