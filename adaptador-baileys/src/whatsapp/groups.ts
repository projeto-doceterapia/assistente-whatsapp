export interface GroupInfo {
  jid: string
  subject: string
}

// Baileys returns a record keyed by group id with participants and settings.
// Only the id and the subject are kept so participant data and message content
// can never reach a log.
export function describeGroups(
  groups: Record<string, { id: string; subject?: string }>,
): GroupInfo[] {
  return Object.values(groups)
    .map((group) => ({ jid: group.id, subject: group.subject ?? '' }))
    .sort((a, b) => a.subject.localeCompare(b.subject) || a.jid.localeCompare(b.jid))
}
