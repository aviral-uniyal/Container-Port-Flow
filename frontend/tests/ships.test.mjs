import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { submitJob } from '../src/api.js'

const ships = [
  { ship_id: 11, vessel_name: 'Ocean Star', status: 'NOT ARRIVED' },
  { ship_id: 12, vessel_name: 'Harbor Queen', status: 'IN OPERATION' },
  { ship_id: 13, vessel_name: 'Atlantic Sun', status: 'COMPLETED & LEFT' },
]

let server
let JobForm
let DatabaseInventory
let filterInventoryShips
let matchingShips
let selectableShips
let nextShipIndex

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ default: JobForm, matchingShips, selectableShips, nextShipIndex } =
    await server.ssrLoadModule('/src/components/JobForm.jsx'))
  ;({ default: DatabaseInventory, filterInventoryShips } =
    await server.ssrLoadModule('/src/components/DatabaseInventory.jsx'))
})

after(async () => { await server?.close() })

test('ship search is case-insensitive and only offers unused ships', () => {
  assert.deepEqual(matchingShips(ships, 'OCEAN').map(ship => ship.ship_id), [11])
  assert.deepEqual(matchingShips(ships, 'harbor'), [])
  assert.deepEqual(matchingShips(ships, 'missing'), [])
  assert.deepEqual(selectableShips(ships).map(ship => ship.ship_id), [11])
  assert.equal(nextShipIndex(-1, 3, 'ArrowDown'), 0)
  assert.equal(nextShipIndex(0, 3, 'ArrowUp'), 2)
})

test('ship selector is required before submission and supports keyboard and clearing', async () => {
  const html = renderToStaticMarkup(React.createElement(JobForm, {
    onSubmit: () => {}, ships, inventoryLoading: false, inventoryError: null,
  }))
  assert.match(html, /role="combobox"/)
  assert.match(html, /aria-required="true"/)
  assert.match(html, /Search and select a ship/)
  assert.match(html, /Each ship can have one job per run/)
  assert.match(html, /id="btn-submit-job"[^>]*disabled=""/)
  const css = await readFile(new URL('../src/index.css', import.meta.url), 'utf8')
  for (const selector of ['.ship-selector__options', '.ships-inventory__list']) {
    const block = css.slice(css.indexOf(`${selector} {`)).split('}')[0]
    assert.match(block, /max-height:\s*240px/)
    assert.match(block, /overflow-y:\s*auto/)
  }
  assert.match(css, /\.ship-selector__clear/)
})

test('unavailable inventory does not imply ships are selectable', () => {
  const html = renderToStaticMarkup(React.createElement(JobForm, {
    onSubmit: () => {}, ships: null, inventoryError: 'Database unavailable',
  }))
  assert.match(html, /Ship inventory unavailable: Database unavailable/)
  assert.match(html, /id="btn-submit-job"[^>]*disabled=""/)
})

test('submission sends numeric ship_id and preserves existing job fields', async () => {
  const originalFetch = globalThis.fetch
  let request
  globalThis.fetch = async (url, options) => {
    request = { url, options }
    return { ok: true, json: async () => ({ job_id: 'J1' }) }
  }
  try {
    const response = await submitJob({
      ship_id: 11, job_id: 'J1', arrival_time: 2, work_total: 8,
      priority: 1, berths: 1, cranes: 2, trucks: 0,
    })
    assert.equal(response.ok, true)
    assert.equal(request.url, '/simulation/jobs')
    assert.deepEqual(JSON.parse(request.options.body), {
      ship_id: 11, job_id: 'J1', arrival_time: 2, work_total: 8,
      priority: 1, berths: 1, cranes: 2, trucks: 0,
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('duplicate and invalid ship rejections surface the backend message', async () => {
  const originalFetch = globalThis.fetch
  try {
    for (const [status, detail] of [
      [409, 'Ship already has a job in this run'],
      [404, 'Selected ship was not found'],
    ]) {
      globalThis.fetch = async () => ({ ok: false, status, json: async () => ({ detail }) })
      const result = await submitJob({ ship_id: 11, job_id: 'J1' })
      assert.equal(result.ok, false)
      assert.equal(result.error, detail)
    }
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('inventory renders total count and all three backend ship status labels', () => {
  assert.deepEqual(filterInventoryShips(ships, '  QUEEN ').map(ship => ship.ship_id), [12])
  assert.deepEqual(filterInventoryShips(ships, 'no such ship'), [])
  const html = renderToStaticMarkup(React.createElement(DatabaseInventory, {
    inventory: { terminals: [], berths: [], cranes: [], ships },
  }))
  assert.match(html, /Ships <span>\(3 total\)<\/span>/)
  assert.match(html, /Ocean Star/)
  assert.match(html, /Harbor Queen/)
  assert.match(html, /Atlantic Sun/)
  for (const status of ['NOT ARRIVED', 'IN OPERATION', 'COMPLETED &amp; LEFT']) {
    assert.match(html, new RegExp(status))
  }
  assert.match(html, /id="db-ships-search"/)
})

test('fresh inventory responses replace prior statuses after completion and refresh', () => {
  const render = currentShips => renderToStaticMarkup(React.createElement(DatabaseInventory, {
    inventory: { terminals: [], berths: [], cranes: [], ships: currentShips },
  }))
  const started = render([{ ...ships[0], status: 'IN OPERATION' }])
  const completed = render([{ ...ships[0], status: 'COMPLETED & LEFT' }])
  const refreshed = render([{ ...ships[0], status: 'NOT ARRIVED' }])
  assert.match(started, /IN OPERATION/)
  assert.match(completed, /COMPLETED &amp; LEFT/)
  assert.match(refreshed, /NOT ARRIVED/)
  assert.doesNotMatch(completed, /IN OPERATION/)
})
