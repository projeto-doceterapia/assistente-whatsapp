// Bounds on the registry of self-sent message ids. They are constants, not env
// vars, because they only guard memory: 500 ids over 15 minutes is far more
// than the echo window ever needs, and a single tester never approaches it.
export const SENT_REGISTRY_MAX_IDS = 500
export const SENT_REGISTRY_TTL_MS = 15 * 60 * 1000

// Clock is injected so tests can drive expiry deterministically instead of
// sleeping; in production it is simply Date.now.
export type Clock = () => number

// Tracks the ids of messages the adapter itself sent, so that their `fromMe`
// echoes can be told apart from messages the tester actually typed. The map
// keeps insertion order, which both the TTL sweep and the capacity eviction
// rely on: the oldest entry is always the first key.
export class SentMessageRegistry {
  private readonly ids = new Map<string, number>()

  constructor(
    private readonly now: Clock = () => Date.now(),
    private readonly maxIds: number = SENT_REGISTRY_MAX_IDS,
    private readonly ttlMs: number = SENT_REGISTRY_TTL_MS,
  ) {}

  // Records an id just sent. Re-recording refreshes its timestamp and moves it
  // to the newest position so it is evicted last.
  add(id: string): void {
    this.ids.delete(id)
    this.ids.set(id, this.now())
    this.evictExpired()
    this.evictOverCapacity()
  }

  // True when the id was sent by the adapter and has not yet expired. Expired
  // entries are swept first so a stale id is never treated as our own.
  has(id: string): boolean {
    this.evictExpired()
    return this.ids.has(id)
  }

  private evictExpired(): void {
    const cutoff = this.now() - this.ttlMs
    for (const [id, at] of this.ids) {
      // Insertion order is also age order, so the first non-expired entry means
      // every later one is still fresh and the sweep can stop.
      if (at > cutoff) {
        break
      }
      this.ids.delete(id)
    }
  }

  private evictOverCapacity(): void {
    while (this.ids.size > this.maxIds) {
      const oldest = this.ids.keys().next().value
      if (oldest === undefined) {
        break
      }
      this.ids.delete(oldest)
    }
  }
}
