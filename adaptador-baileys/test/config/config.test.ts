import assert from 'node:assert/strict'
import { test } from 'node:test'
import { loadConfig } from '../../src/config/config.js'

test('requires secrets and group when baileys is enabled', () => {
  assert.throws(() => loadConfig({ BAILEYS_ENABLED: 'true' }), /GRUPO_JID/)
})

test('loads a full config when all required vars are present', () => {
  const config = loadConfig({
    BAILEYS_ENABLED: 'true',
    GRUPO_JID: 'g@g.us',
    ADAPTER_SECRET: 's1',
    WEBHOOK_URL: 'http://orq/hook',
    WEBHOOK_SECRET: 's2',
  })
  assert.equal(config.baileysEnabled, true)
  assert.equal(config.grupoJid, 'g@g.us')
  assert.equal(config.port, 8080)
})

test('does not require secrets when baileys is disabled', () => {
  const config = loadConfig({ BAILEYS_ENABLED: 'false' })
  assert.equal(config.baileysEnabled, false)
  assert.equal(config.grupoJid, '')
})

test('group discovery is off by default and turns on only with DISCOVER_GROUPS=true', () => {
  assert.equal(loadConfig({ BAILEYS_ENABLED: 'false' }).discoverGroups, false)
  assert.equal(loadConfig({ BAILEYS_ENABLED: 'false', DISCOVER_GROUPS: 'true' }).discoverGroups, true)
  assert.equal(loadConfig({ BAILEYS_ENABLED: 'false', DISCOVER_GROUPS: 'yes' }).discoverGroups, false)
})

test('defaults the media size cap to 8 MiB and honours the override', () => {
  const def = loadConfig({ BAILEYS_ENABLED: 'false' })
  assert.equal(def.mediaMaxBytes, 8388608)
  const custom = loadConfig({ BAILEYS_ENABLED: 'false', MEDIA_MAX_BYTES: '1024' })
  assert.equal(custom.mediaMaxBytes, 1024)
})
