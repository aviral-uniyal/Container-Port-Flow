import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { recoverCrane, registerCrane } from '../src/api.js'

let server
let CraneRegistry
let App
let stabilizeCraneOrder

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ default: CraneRegistry } = await server.ssrLoadModule('/src/components/CraneRegistry.jsx'))
  ;({ default: App } = await server.ssrLoadModule('/src/App.jsx'))
  ;({ stabilizeCraneOrder } = await server.ssrLoadModule('/src/uiLogic.js'))
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

test('registration order C1, C2, C3 survives failure, recovery, and Refresh snapshots', () => {
  let order = []
  for (const id of ['C1', 'C2', 'C3']) order = [...order, id]
  for (const status of ['AVAILABLE', 'FAILED', 'ASSIGNED', 'AVAILABLE']) {
    const snapshot = ['C3', 'C1', 'C2'].map(crane_id => ({
      crane_id, status: crane_id === 'C1' ? status : 'AVAILABLE',
    }))
    const stable = stabilizeCraneOrder(snapshot, order)
    assert.deepEqual(stable.order, ['C1', 'C2', 'C3'])
    const html = renderToStaticMarkup(React.createElement(CraneRegistry, { cranes: stable.cranes }))
    assert.ok(html.indexOf('C1</code>') < html.indexOf('C2</code>'))
    assert.ok(html.indexOf('C2</code>') < html.indexOf('C3</code>'))
  }
  assert.deepEqual(stabilizeCraneOrder([{ crane_id: 'C1' }], order).order, order)
})

test('registration closes at initialized capacity and reports the limit', () => {
  const html = renderToStaticMarkup(React.createElement(CraneRegistry, {
    cranes: [{ crane_id: 'C1', status: 'AVAILABLE' }],
    capacity: 1,
    inventory: { cranes: [{ crane_id: 'C1', status: 'AVAILABLE' }, { crane_id: 'C2', status: 'AVAILABLE' }] },
    onRegister: () => {},
  }))
  assert.match(html, /Crane registration limit reached \(1\/1\)/)
  assert.match(html, /<select[^>]*disabled=""/)
  assert.doesNotMatch(html, /<option[^>]*value="C2"/)
})

test('capacity rejection reaches the frontend as a clear error', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => ({
    ok: false,
    status: 409,
    json: async () => ({ detail: 'Crane registration limit reached (capacity 1).' }),
  })
  try {
    const result = await registerCrane('C2')
    assert.equal(result.ok, false)
    assert.match(result.error, /registration limit reached/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('automatic Run polling refreshes inventory without showing a loading placeholder', async () => {
  const source = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
  const poll = source.split('async function poll() {')[1].split('timerId = window.setTimeout(poll, 1000)')[0]
  assert.match(poll, /refreshInventory\(\{ silent: true \}\)/)
  assert.doesNotMatch(poll, /location\.reload|window\.location|setInventoryLoading/)
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
