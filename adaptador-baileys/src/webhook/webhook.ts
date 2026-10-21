import { signBody, SIGNATURE_HEADER } from './hmac.js'
import type { AdapterEvent } from '../whatsapp/types.js'

export type Fetch = typeof fetch

// Forwards an event to the orchestrator webhook, signing the exact bytes sent
// with HMAC-SHA256 so the orchestrator can verify integrity and origin.
export class WebhookForwarder {
  constructor(
    private readonly url: string,
    private readonly secret: string,
    private readonly fetchImpl: Fetch = fetch,
  ) {}

  async forward(event: AdapterEvent): Promise<void> {
    const body = JSON.stringify(event)
    const signature = signBody(this.secret, body)
    const response = await this.fetchImpl(this.url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        [SIGNATURE_HEADER]: signature,
      },
      body,
    })
    if (!response.ok) {
      throw new Error(`Webhook rejected event with status ${response.status}`)
    }
  }
}
