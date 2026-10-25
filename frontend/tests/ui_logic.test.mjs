import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { dispatchBatch, resetSimulation } from '../src/api.js'

let server
let App
let JobsTable
let StatusBar
let dispatchFeedback
let blockedExecutionFeedback
let executionStatusLabel
let stabilizeJobOrder
let insertJobOrder
let previewJobOrder
let stepFeedback

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ blockedExecutionFeedback, dispatchFeedback, executionStatusLabel,
    insertJobOrder, previewJobOrder, stabilizeJobOrder, stepFeedback } =
    await server.ssrLoadModule('/src/uiLogic.js'))
  ;({ default: App } = await server.ssrLoadModule('/src/App.jsx'))
  ;({ default: JobsTable } = await server.ssrLoadModule('/src/components/JobsTable.jsx'))
  ;({ default: StatusBar } = await server.ssrLoadModule('/src/components/StatusBar.jsx'))
})

after(async () => {
  await server?.close()
})

const ids = result => result.jobs.map(job => job.job_id)

test('FCFS inserts earlier arrival immediately and preserves submission ties', () => {
  const first = { job_id: 'JOB_001', arrival_time: 5, submission_seq: 0, work_total: 2 }
  const second = { job_id: 'JOB_002', arrival_time: 3, submission_seq: 1, work_total: 2 }
  assert.deepEqual(insertJobOrder(['JOB_001'], [first], second, 'FCFS'), ['JOB_002', 'JOB_001'])
  assert.deepEqual(previewJobOrder([first, { ...second, arrival_time: 5 }], 'FCFS'),
    ['JOB_001', 'JOB_002'])
})

test('SJF and Priority choose among arrived jobs at submission', () => {
  const jobs = [
    { job_id: 'A', arrival_time: 0, work_total: 3, priority: 3, submission_seq: 0 },
    { job_id: 'B', arrival_time: 1, work_total: 8, priority: 2, submission_seq: 1 },
    { job_id: 'C', arrival_time: 2, work_total: 1, priority: 1, submission_seq: 2 },
  ]
  assert.deepEqual(previewJobOrder(jobs, 'SJF'), ['A', 'C', 'B'])
  assert.deepEqual(previewJobOrder(jobs, 'PRIORITY'), ['A', 'C', 'B'])
  assert.deepEqual(insertJobOrder(['A', 'B'], jobs.slice(0, 2), jobs[2], 'SJF'), ['A', 'C', 'B'])
  assert.deepEqual(insertJobOrder(['A', 'B'], jobs.slice(0, 2), jobs[2], 'PRIORITY'), ['A', 'C', 'B'])
})

test('execution snapshots, interruption, polling, Refresh, and completion preserve rows', () => {
  const order = ['B', 'A']
  for (const state of ['PENDING', 'ACTIVE', 'INTERRUPTED', 'COMPLETED']) {
    const snapshot = [
      { job_id: 'A', state, queue_position: 1, dispatch_sequence: 0, work_remaining: 0 },
      { job_id: 'B', state, queue_position: 2, dispatch_sequence: 1, work_remaining: 9 },
    ]
    assert.deepEqual(stabilizeJobOrder(snapshot, order).order, order)
    assert.deepEqual(stabilizeJobOrder([...snapshot].reverse(), order).order, order)
  }
})

test('new submission inserts without changing relative positions of existing rows', () => {
  const jobs = [
    { job_id: 'A', arrival_time: 5, submission_seq: 0, work_total: 2 },
    { job_id: 'B', arrival_time: 3, submission_seq: 1, work_total: 2 },
  ]
  const next = insertJobOrder(['B', 'A'], jobs,
    { job_id: 'C', arrival_time: 4, submission_seq: 2, work_total: 1 }, 'FCFS')
  assert.deepEqual(next, ['B', 'C', 'A'])
})

test('a temporarily incomplete snapshot cannot erase established positions', () => {
  const partial = stabilizeJobOrder([{ job_id: 'A' }], ['B', 'A'])
  assert.deepEqual(partial.order, ['B', 'A'])
  assert.deepEqual(stabilizeJobOrder([{ job_id: 'A' }, { job_id: 'B' }], partial.order).order,
    ['B', 'A'])
})

test('Jobs table explains scheduling and only queued jobs show queue positions', () => {
  const ordered = stabilizeJobOrder([
    { job_id: 'DONE', state: 'COMPLETED', submission_seq: 0, priority: 3, dispatch_sequence: 3 },
    { job_id: 'WAITING', state: 'PENDING', submission_seq: 1, priority: 1, queue_position: 1 },
    { job_id: 'RUNNING', state: 'ACTIVE', submission_seq: 2, priority: 2, crane_fulfilled: true, dispatch_sequence: 0 },
    { job_id: 'DAMAGED', state: 'ACTIVE', submission_seq: 3, priority: 3, crane_fulfilled: false, dispatch_sequence: 1 },
    { job_id: 'STOPPED', state: 'INTERRUPTED', submission_seq: 4, priority: 4, crane_fulfilled: false, dispatch_sequence: 2 },
  ], ['RUNNING', 'DAMAGED', 'STOPPED', 'DONE', 'WAITING']).jobs
  const html = renderToStaticMarkup(React.createElement(JobsTable, { jobs: ordered }))
  assert.match(html, /Queue Position/)
  assert.match(html, /Arrival Time/)
  assert.match(html, /Crane Supply/)
  assert.match(html, /Jobs are placed by the selected scheduling policy when submitted/)
  assert.match(html, /Rows keep their positions/)
  assert.match(html, /State and crane supply are shown separately/)
  assert.match(html, /FULLY SUPPLIED/)
  assert.equal((html.match(/UNDER-SUPPLIED/g) ?? []).length, 2)
  assert.ok(html.indexOf('RUNNING') < html.indexOf('DONE'))
  assert.ok(html.indexOf('DONE') < html.indexOf('WAITING'))
  assert.match(html, /WAITING[\s\S]*?<td>1<\/td>/)
})

test('dashboard omits the simulation-time display while retaining clock-based feedback', async () => {
  const html = renderToStaticMarkup(React.createElement(App))
  const source = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
  const css = await readFile(new URL('../src/index.css', import.meta.url), 'utf8')
  assert.doesNotMatch(html, /Simulation Time:/)
  assert.doesNotMatch(source, /setSimulationTime|simulation-time|Simulation Time:/)
  assert.doesNotMatch(css, /\.simulation-time\b/)
  assert.match(source, /snapshotResult\.data\.simulation_time/)
})

test('Dispatch All feedback distinguishes empty, waiting, and success', () => {
  assert.equal(dispatchFeedback({ pendingJobs: 0 }).message, 'No pending jobs to dispatch.')
  assert.equal(
    dispatchFeedback({ pendingJobs: 2 }).message,
    'Pending jobs are waiting for available resources.',
  )
  assert.equal(
    dispatchFeedback({ pendingJobs: 2, futureJobs: 2 }).message,
    'Pending jobs are waiting for their arrival time.',
  )
  assert.equal(
    dispatchFeedback({ count: 2, dispatchedJobs: ['J1', 'J2'], pendingJobs: 0 }).message,
    'Dispatched 2 jobs: J1, J2.',
  )
})

test('Dispatch All requests dispatch without announcing automatic completion', async () => {
  const originalFetch = globalThis.fetch
  let request
  globalThis.fetch = async (url, options) => {
    request = { url, options }
    return { ok: true, json: async () => ({ count: 2, dispatched_jobs: ['J1', 'J2'] }) }
  }
  try {
    const result = await dispatchBatch()
    assert.equal(result.ok, true)
    assert.equal(request.url, '/simulation/dispatch')
    assert.equal(request.options.method, 'POST')
    assert.match(dispatchFeedback({ count: result.data.count, dispatchedJobs: result.data.dispatched_jobs }).message, /Dispatched 2 jobs/)
    const source = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
    const handler = source.split('async function handleDispatch() {')[1].split('async function handleJobSubmit')[0]
    assert.match(handler, /await refreshSnapshot\(\)/)
    assert.match(handler, /await refreshExecution\(\)/)
    assert.match(handler, /await refreshInventory\(\)/)
    assert.doesNotMatch(handler, /refreshExecution\(\{ announce: true \}\)/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('Step feedback reports progress, completion, batch no-op, and resource blocking', () => {
  assert.match(stepFeedback({}, [{ state: 'PENDING', arrival_time: 5 }], false, 1).message,
    /waiting for the next job arrival/)
  assert.match(stepFeedback({ advanced_jobs: ['J1'] }).message, /advanced 1 active job/)
  assert.match(stepFeedback({ completed_jobs: ['J1'] }).message, /Finished: J1/)
  assert.match(
    stepFeedback({}, [{ job_id: 'J1', state: 'PENDING' }], true).message,
    /Dispatch All or Run/,
  )
  assert.match(
    stepFeedback({}, [{ job_id: 'J1', state: 'INTERRUPTED' }], false).message,
    /blocked by unavailable resources/,
  )
})

test('blocked execution distinguishes resource waits from backend failures', () => {
  assert.deepEqual(blockedExecutionFeedback(null), {
    message: 'Execution is blocked by unavailable resources or interrupted work.',
    severity: 'warn',
  })
  const persistence = blockedExecutionFeedback(
    'Database persistence failed; reconciliation required: missing column',
  )
  assert.equal(persistence.severity, 'err')
  assert.match(persistence.message, /backend could not safely continue/)
  assert.match(persistence.message, /Database persistence failed/)
})

test('execution status displays the idle backend state as NOT RUNNING', () => {
  assert.equal(executionStatusLabel('not_running'), 'NOT RUNNING')
  assert.equal(executionStatusLabel('paused'), 'PAUSED')
})

test('Reset is beside Refresh and confirmation guards the API request', async () => {
  const html = renderToStaticMarkup(React.createElement(App))
  assert.ok(html.indexOf('btn-refresh-snapshot') < html.indexOf('btn-reset-simulation'))
  assert.match(html, /id="btn-reset-simulation"[^>]*disabled=""/)
  const source = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
  const handler = source.split('async function handleReset() {')[1].split('\n  return (')[0]
  assert.match(handler, /if \(!window\.confirm\([\s\S]*?\)\) return[\s\S]*?resetSimulation\(\)/)
})

test('Reset API calls the dedicated endpoint', async () => {
  const originalFetch = globalThis.fetch
  let request
  globalThis.fetch = async (url, options) => {
    request = { url, options }
    return { ok: true, json: async () => ({ ok: true }) }
  }
  try {
    assert.equal((await resetSimulation()).ok, true)
    assert.deepEqual(request, { url: '/simulation/reset', options: { method: 'POST' } })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('connection bar excludes operational messages', () => {
  const html = renderToStaticMarkup(React.createElement(StatusBar, {
    connectionStatus: 'ok',
    healthDetail: { bridge_alive: true, db_connected: true },
    message: 'Job submitted.',
  }))
  assert.match(html, /Backend connected/)
  assert.match(html, /Bridge/)
  assert.match(html, /DB/)
  assert.doesNotMatch(html, /Job submitted/)
})

test('Dashboard is the default tab and inventory stays mounted in its hidden view', async () => {
  const html = renderToStaticMarkup(React.createElement(App))
  assert.match(html, /aria-label="Main views"/)
  assert.match(html, /aria-current="page" aria-controls="dashboard-view"/)
  assert.match(html, /aria-controls="inventory-view"/)
  assert.match(html, /id="dashboard-view" class="app-main__view">/)
  assert.match(html, /id="inventory-view" class="app-main__view" hidden=""/)
  const dashboard = html.split('id="dashboard-view"')[1].split('id="inventory-view"')[0]
  const inventory = html.split('id="inventory-view"')[1]
  const labels = [
    'Connection status',
    'Resource Summary',
    'Operational feedback',
    'Simulation Setup',
    'Job Management',
    'Simulation Execution',
    'Simulation Jobs',
    'Crane Registry',
  ]
  const positions = labels.map(label => dashboard.indexOf(label))
  assert.ok(positions.every(position => position >= 0))
  assert.deepEqual([...positions].sort((a, b) => a - b), positions)
  assert.doesNotMatch(dashboard, /Database Inventory/)
  assert.match(inventory, /Database Inventory/)
  assert.match(dashboard, /NOT RUNNING/)
  const source = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(source, /onClick=\{\(\) => setActiveTab\('inventory'\)\}/)
  assert.match(source, /onClick=\{\(\) => setActiveTab\('dashboard'\)\}/)
  assert.match(source, /hidden=\{activeTab !== 'dashboard'\}/)
  assert.match(source, /hidden=\{activeTab !== 'inventory'\}/)
  assert.ok(html.indexOf('btn-refresh-snapshot') < html.indexOf('id="dashboard-view"'))
  assert.ok(html.indexOf('btn-reset-simulation') < html.indexOf('id="dashboard-view"'))
})
