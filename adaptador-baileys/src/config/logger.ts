import pino from 'pino'

export const logger = pino({
  level: process.env['LOG_LEVEL'] ?? 'info',
  // Never serialize message bodies or media; only structural metadata is logged.
  redact: {
    paths: ['text', 'body', 'message', 'content', 'caption'],
    remove: true,
  },
})

export type Logger = typeof logger
