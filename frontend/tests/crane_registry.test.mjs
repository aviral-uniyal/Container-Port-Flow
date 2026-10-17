import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

let server
let CraneRegistry

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ default: CraneRegistry } = await server.ssrLoadModule('/src/components/CraneRegistry.jsx'))
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
