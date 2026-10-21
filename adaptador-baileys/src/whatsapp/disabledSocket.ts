import type { HealthState, MediaPayload, WaSocket } from './types.js'

// A socket with no WhatsApp connection, used when BAILEYS_ENABLED=false so the
// image can start and answer GET /health for standalone verification. It
// reports "degraded" because the HTTP layer is up but no session is paired.
export class DisabledWaSocket implements WaSocket {
  async sendText(): Promise<void> {
    throw new Error('WhatsApp connection is disabled (BAILEYS_ENABLED=false)')
  }

  async downloadMedia(): Promise<MediaPayload> {
    throw new Error('WhatsApp connection is disabled (BAILEYS_ENABLED=false)')
  }

  health(): HealthState {
    return 'degraded'
  }

  async close(): Promise<void> {
    // nothing to close
  }
}
