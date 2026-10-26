export interface AdapterConfig {
  port: number
  host: string
  sessionDir: string
  grupoJid: string
  adapterSecret: string
  webhookUrl: string
  webhookSecret: string
  baileysEnabled: boolean
  discoverGroups: boolean
  mediaTtlMs: number
  mediaMaxBytes: number
}

// Image mime types the adapter accepts for download; anything else is rejected
// before it is buffered or streamed back through the HTTP API.
export const ALLOWED_MEDIA_MIMETYPES: ReadonlySet<string> = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
])

function required(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name]
  if (value === undefined || value === '') {
    throw new Error(`Missing required environment variable: ${name}`)
  }
  return value
}

function optional(env: NodeJS.ProcessEnv, name: string, fallback: string): string {
  const value = env[name]
  return value === undefined || value === '' ? fallback : value
}

// BAILEYS_ENABLED=false lets the service start and reach a healthy HTTP state
// without a real WhatsApp connection, for standalone image verification.
export function loadConfig(env: NodeJS.ProcessEnv = process.env): AdapterConfig {
  const baileysEnabled = optional(env, 'BAILEYS_ENABLED', 'true').toLowerCase() !== 'false'

  // GRUPO_JID, WEBHOOK_URL and secrets are only required when the real
  // connection is on; disabled mode is for image/health verification only.
  const read = baileysEnabled
    ? (name: string) => required(env, name)
    : (name: string) => optional(env, name, '')

  return {
    port: Number(optional(env, 'PORT', '8080')),
    host: optional(env, 'HOST', '0.0.0.0'),
    sessionDir: optional(env, 'SESSION_DIR', './session'),
    grupoJid: read('GRUPO_JID'),
    adapterSecret: read('ADAPTER_SECRET'),
    webhookUrl: read('WEBHOOK_URL'),
    webhookSecret: read('WEBHOOK_SECRET'),
    baileysEnabled,
    discoverGroups: optional(env, 'DISCOVER_GROUPS', 'false').toLowerCase() === 'true',
    mediaTtlMs: Number(optional(env, 'MEDIA_TTL_MS', '120000')),
    mediaMaxBytes: Number(optional(env, 'MEDIA_MAX_BYTES', '8388608')),
  }
}
