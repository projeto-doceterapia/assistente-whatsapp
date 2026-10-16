import Fastify, { type FastifyInstance } from 'fastify'
import { timingSafeEqual } from 'node:crypto'
import type { MediaRefStore } from '../media/mediaStore.js'
import { MediaRejectedError, type HealthState, type WaSocket } from '../whatsapp/types.js'

export interface ServerDeps {
  socket: WaSocket
  media: MediaRefStore
  adapterSecret: string
  sendAllowedJid: string
}

const ADAPTER_SECRET_HEADER = 'x-adapter-secret'

function secretMatches(expected: string, received: unknown): boolean {
  if (typeof received !== 'string' || received.length === 0) {
    return false
  }
  const a = Buffer.from(expected)
  const b = Buffer.from(received)
  if (a.length !== b.length) {
    return false
  }
  return timingSafeEqual(a, b)
}

interface SendBody {
  text?: unknown
  to?: unknown
}

// Builds the adapter HTTP API. GET /health is public and never exposes
// secrets; the inbound endpoints require the shared ADAPTER_SECRET.
export function buildServer(deps: ServerDeps): FastifyInstance {
  const app = Fastify({ logger: false })

  app.get('/health', async () => {
    const state: HealthState = deps.socket.health()
    return { status: state }
  })

  app.post('/messages', async (request, reply) => {
    if (!secretMatches(deps.adapterSecret, request.headers[ADAPTER_SECRET_HEADER])) {
      return reply.code(401).send({ error: 'unauthorized' })
    }
    const body = (request.body ?? {}) as SendBody
    if (typeof body.text !== 'string' || body.text.length === 0) {
      return reply.code(400).send({ error: 'text is required' })
    }
    // The adapter only ever sends to the dedicated group; a caller-supplied
    // target is ignored to keep the linked account scoped to one chat.
    await deps.socket.sendText(deps.sendAllowedJid, body.text)
    return reply.code(202).send({ status: 'queued' })
  })

  app.get<{ Params: { ref: string } }>('/media/:ref', async (request, reply) => {
    if (!secretMatches(deps.adapterSecret, request.headers[ADAPTER_SECRET_HEADER])) {
      return reply.code(401).send({ error: 'unauthorized' })
    }
    const messageId = deps.media.resolve(request.params.ref)
    if (messageId === null) {
      return reply.code(404).send({ error: 'not found' })
    }
    try {
      const payload = await deps.socket.downloadMedia(messageId)
      reply.header('content-type', payload.mimetype)
      return reply.send(payload.data)
    } catch (err) {
      // Oversized or disallowed media is refused before it is streamed back;
      // the error carries the exact status (413 too large, 415 wrong type).
      if (err instanceof MediaRejectedError) {
        return reply.code(err.status).send({ error: err.message })
      }
      throw err
    }
  })

  return app
}
