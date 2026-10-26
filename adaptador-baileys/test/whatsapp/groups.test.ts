import assert from 'node:assert/strict'
import { test } from 'node:test'
import { describeGroups } from '../../src/whatsapp/groups.js'

test('keeps only the id and the subject of each group', () => {
  const groups = describeGroups({
    'a@g.us': { id: 'a@g.us', subject: 'Doceterapia', participants: [{ id: 'p@s.whatsapp.net' }] } as never,
  })
  assert.deepEqual(groups, [{ jid: 'a@g.us', subject: 'Doceterapia' }])
})

test('sorts by subject and then by id, so the output is stable', () => {
  const groups = describeGroups({
    'c@g.us': { id: 'c@g.us', subject: 'Zeta' },
    'b@g.us': { id: 'b@g.us', subject: 'Alfa' },
    'a@g.us': { id: 'a@g.us', subject: 'Alfa' },
  })
  assert.deepEqual(
    groups.map((group) => group.jid),
    ['a@g.us', 'b@g.us', 'c@g.us'],
  )
})

test('accepts a group without a subject', () => {
  assert.deepEqual(describeGroups({ 'a@g.us': { id: 'a@g.us' } }), [{ jid: 'a@g.us', subject: '' }])
})

test('returns an empty list when the account has no groups', () => {
  assert.deepEqual(describeGroups({}), [])
})
