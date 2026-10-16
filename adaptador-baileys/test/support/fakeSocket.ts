import type { HealthState, MediaPayload, WaSocket } from '../../src/whatsapp/types.js'

// Minimal in-memory WaSocket used by tests so the HTTP layer, allowlist,
// media refs and health can be exercised without a real WhatsApp connection.
export class FakeWaSocket implements WaSocket {
  readonly sent: Array<{ to: string; text: string }> = []
  state: HealthState = 'connected'
  media: MediaPayload = { data: Buffer.from('fake'), mimetype: 'image/jpeg' }
  // When set, downloadMedia rejects with this error instead of returning media,
  // so the HTTP layer's error-to-status mapping can be exercised.
  downloadError: Error | null = null

  async sendText(to: string, text: string): Promise<void> {
    this.sent.push({ to, text })
  }

  async downloadMedia(): Promise<MediaPayload> {
    if (this.downloadError !== null) {
      throw this.downloadError
    }
    return this.media
  }

  health(): HealthState {
    return this.state
  }

  async close(): Promise<void> {
    // nothing to close
  }
}
