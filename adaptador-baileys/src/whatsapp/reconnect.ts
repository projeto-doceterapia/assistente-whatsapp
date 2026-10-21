export interface ReconnectDecision {
  reconnect: boolean
  delayMs: number
}

export interface BackoffOptions {
  baseMs: number
  maxMs: number
}

// Pure reconnect policy: do not reconnect after an explicit logout, otherwise
// use exponential backoff with full jitter bounded by maxMs.
export function decideReconnect(
  loggedOut: boolean,
  attempt: number,
  options: BackoffOptions,
  random: () => number = Math.random,
): ReconnectDecision {
  if (loggedOut) {
    return { reconnect: false, delayMs: 0 }
  }
  const safeAttempt = attempt < 0 ? 0 : attempt
  const exponential = Math.min(options.maxMs, options.baseMs * 2 ** safeAttempt)
  const delayMs = Math.floor(random() * exponential)
  return { reconnect: true, delayMs }
}
