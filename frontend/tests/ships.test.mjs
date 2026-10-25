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
let submittedShipSelection
let clearSubmittedShip

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ default: JobForm, clearSubmittedShip, matchingShips, selectableShips,
    nextShipIndex, submittedShipSelection } =
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

test('cancelled ships remain unavailable in the current run but can be reused in a new run', () => {
  const cancelledInCurrentRun = { ship_id: 14, vessel_name: 'Returning Ship', status: 'NOT ARRIVED', is_selectable: false }
  const newRun = { ...cancelledInCurrentRun, is_selectable: true }
  assert.deepEqual(selectableShips([cancelledInCurrentRun]), [])
  assert.deepEqual(matchingShips([cancelledInCurrentRun], 'returning'), [])
  assert.deepEqual(selectableShips([newRun]).map(ship => ship.ship_id), [14])
  assert.deepEqual(matchingShips([newRun], 'returning').map(ship => ship.ship_id), [14])
})

test('successful submission resets selection without reopening the dropdown', async () => {
  assert.deepEqual(submittedShipSelection(), {
    ship_id: null, query: '', open: false, activeIndex: -1,
  })
  const source = await readFile(new URL('../src/components/JobForm.jsx', import.meta.url), 'utf8')
  assert.match(source, /if \(submitted\) resetShipAfterSubmit\(\)/)
  assert.match(source, /function resetShipAfterSubmit\(\)[\s\S]*?shipSearchRef\.current\?\.blur\(\)/)
  assert.match(source, /placeholder="Select a ship"/)
  assert.deepEqual(clearSubmittedShip({
    ship_id: 11, job_id: 'J1', work_total: 8, priority: 2,
  }), {
    ship_id: null, job_id: 'J1', work_total: 8, priority: 2,
  })

  const appSource = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(appSource, /void Promise\.all\([\s\S]*?refreshInventory\(\)[\s\S]*?return true/)
})

test('ship selector is required before submission and supports keyboard and clearing', async () => {
  const html = renderToStaticMarkup(React.createElement(JobForm, {
    onSubmit: () => {}, ships, inventoryLoading: false, inventoryError: null,
  }))
  assert.match(html, /role="combobox"/)
  assert.match(html, /aria-required="true"/)
  assert.match(html, /Select a ship/)
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

test('an ordinary inventory refresh keeps an existing ship list usable', () => {
  const html = renderToStaticMarkup(React.createElement(JobForm, {
    onSubmit: () => {}, ships, inventoryLoading: true, inventoryError: null,
  }))
  assert.match(html, /id="job-ship-search"/)
  assert.doesNotMatch(html, /id="job-ship-search"[^>]*disabled=""/)
  assert.doesNotMatch(html, /Loading ships from PostgreSQL/)
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

test('database inventory retains all four detailed record lists', () => {
  const inventory = {
    terminals: [{ terminal_id: 1, terminal_name: 'Terminal One', description: 'Main terminal' }],
    berths: [{ berth_id: 1, berth_name: 'Berth One', terminal_id: 1 }],
    cranes: Array.from({ length: 12 }, (_, i) => ({
      crane_id: `C${i + 1}`, terminal_id: 1, status: 'AVAILABLE', updated_at: null,
    })),
    ships: Array.from({ length: 179 }, (_, i) => ({
      ship_id: i + 1, vessel_name: `Ship ${i + 1}`, status: 'NOT ARRIVED',
    })),
  }
  const html = renderToStaticMarkup(React.createElement(DatabaseInventory, { inventory }))
  for (const label of ['Terminal records', 'Berth records', 'Crane records from database', 'Ships from PostgreSQL']) {
    assert.ok(html.includes(label))
  }
  assert.match(html, /Terminal One/)
  assert.match(html, /Berth One/)
  for (let i = 1; i <= 12; i += 1) {
    assert.ok(html.includes(`C${i}</code>`))
  }
  assert.match(html, /Ships <span>\(179 total\)<\/span>/)
  assert.equal((html.match(/class="ships-inventory__row"/g) ?? []).length, 179)
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
