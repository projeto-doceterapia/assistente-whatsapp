import { ALLOWED_MEDIA_MIMETYPES } from '../config/config.js'
import { MediaRejectedError } from '../whatsapp/types.js'

// Rejects a media type that is not an allowed image before any bytes are read.
export function assertAllowedMimetype(mimetype: string): void {
  if (!ALLOWED_MEDIA_MIMETYPES.has(mimetype)) {
    throw new MediaRejectedError(`unsupported media type: ${mimetype}`, 415)
  }
}

// Rejects early using the declared size so an oversized file is never fetched.
export function assertDeclaredSizeWithinCap(declaredLength: unknown, maxBytes: number): void {
  const declared = Number(declaredLength ?? 0)
  if (declared > maxBytes) {
    throw new MediaRejectedError('media exceeds the configured size cap', 413)
  }
}

// Accumulates stream chunks, aborting as soon as the running total passes the
// cap so an unbounded buffer is never held in memory even if the declared size
// was missing or lied.
export async function readCapped(
  stream: AsyncIterable<Buffer | Uint8Array>,
  maxBytes: number,
): Promise<Buffer> {
  const chunks: Buffer[] = []
  let total = 0
  for await (const chunk of stream) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    total += buffer.length
    if (total > maxBytes) {
      throw new MediaRejectedError('media exceeds the configured size cap', 413)
    }
    chunks.push(buffer)
  }
  return Buffer.concat(chunks)
}
