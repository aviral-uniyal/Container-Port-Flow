import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { recoverCrane } from '../src/api.js'

let server
let CraneRegistry
let App

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ default: CraneRegistry } = await server.ssrLoadModule('/src/components/CraneRegistry.jsx'))
  ;({ default: App } = await server.ssrLoadModule('/src/App.jsx'))
})

after(async () => {
  await server?.close()
})

test('registration dropdown contains only unregistered AVAILABLE inventory cranes', () => {
  const html = renderToStaticMarkup(React.createElement(CraneRegistry, {
    cranes: [{ crane_id: 'C1', status: 'AVAILABLE' }],
    inventory: { cranes: [
      { crane_id: 'C1', status: 'AVAILABLE' },
      { crane_id: 'C2', status: 'AVAILABLE' },
      { crane_id: 'C3', status: 'ASSIGNED' },
      { crane_id: 'C4', status: 'FAILED' },
      { crane_id: 'CRANE_01', status: 'AVAILABLE' },
      { crane_id: 'c1', status: 'AVAILABLE' },
      { crane_id: 'C13', status: 'AVAILABLE' },
    ] },
    onRegister: () => {},
  }))
  const options = html.match(/<option[^>]*value="[^"]+"[^>]*>/g) ?? []
  assert.equal(options.length, 1)
  assert.match(options[0], /value="C2"/)
  assert.doesNotMatch(html, /<input[^>]*id="crane-id-input"/)
})

test('empty inventory offers no way to create an ID', () => {
  const html = renderToStaticMarkup(React.createElement(CraneRegistry, {
    cranes: [], inventory: { cranes: [] }, onRegister: () => {},
  }))
  assert.match(html, /No eligible database cranes are available/)
  assert.match(html, /<select[^>]*disabled=""/)
  assert.doesNotMatch(html, /<input[^>]*id="crane-id-input"/)
})

test('dashboard has no manual Reconcile control', () => {
  const html = renderToStaticMarkup(React.createElement(App))
  assert.doesNotMatch(html, /btn-reconcile-state|btn-crane-reconcile-sync/)
})

test('recovery API exposes a partial C++ success as useful feedback', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => ({
    ok: false,
    status: 503,
    json: async () => ({ detail: {
      code: 'database_sync_failed',
      message: 'Crane recovery succeeded in C++, but PostgreSQL synchronization failed.',
      runtime_changed: true,
      crane_status: 'ASSIGNED',
    } }),
  })
  try {
    const result = await recoverCrane('C1')
    assert.equal(result.ok, false)
    assert.match(result.error, /recovery succeeded in C\+\+/)
    assert.equal(result.partial.crane_status, 'ASSIGNED')
    assert.equal(result.partial.runtime_changed, true)
  } finally {
    globalThis.fetch = originalFetch
  }
})
