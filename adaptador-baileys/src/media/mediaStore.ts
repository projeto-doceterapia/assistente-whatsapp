import { randomUUID } from 'node:crypto'

interface Entry {
  messageId: string
  expiresAt: number
}

// Maps opaque, short-lived refs to the WhatsApp message id whose media is
// downloaded on demand. Refs expire so a leaked ref cannot fetch media later.
export class MediaRefStore {
  private readonly entries = new Map<string, Entry>()

  constructor(
    private readonly ttlMs: number,
    private readonly now: () => number = Date.now,
  ) {}

  issue(messageId: string): string {
    const ref = randomUUID()
    this.entries.set(ref, { messageId, expiresAt: this.now() + this.ttlMs })
    return ref
  }

  resolve(ref: string): string | null {
    const entry = this.entries.get(ref)
    if (entry === undefined) {
      return null
    }
    if (this.now() >= entry.expiresAt) {
      this.entries.delete(ref)
      return null
    }
    return entry.messageId
  }

  // Drops expired refs; callers may run this periodically to bound memory.
  prune(): void {
    const current = this.now()
    for (const [ref, entry] of this.entries) {
      if (current >= entry.expiresAt) {
        this.entries.delete(ref)
      }
    }
  }
}
