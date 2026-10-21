import { createHmac, timingSafeEqual } from 'node:crypto'

export const SIGNATURE_HEADER = 'x-signature-256'
const PREFIX = 'sha256='

// Signs a raw body with HMAC-SHA256 and returns a "sha256=<hex>" value,
// the same shape the orchestrator verifies.
export function signBody(secret: string, body: string | Buffer): string {
  const hex = createHmac('sha256', secret).update(body).digest('hex')
  return `${PREFIX}${hex}`
}

// Constant-time comparison of a received signature against the expected one.
// Returns false for any malformed or mismatched signature instead of throwing.
export function verifySignature(secret: string, body: string | Buffer, received: string | undefined): boolean {
  if (!received) {
    return false
  }
  const expected = signBody(secret, body)
  const a = Buffer.from(expected)
  const b = Buffer.from(received)
  if (a.length !== b.length) {
    return false
  }
  return timingSafeEqual(a, b)
}
