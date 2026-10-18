import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { dispatchBatch } from '../src/api.js'

let server
let App
let StatusBar
let dispatchFeedback
let executionStatusLabel
let stabilizeJobOrder
let stepFeedback

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ dispatchFeedback, executionStatusLabel, stabilizeJobOrder, stepFeedback } =
    await server.ssrLoadModule('/src/uiLogic.js'))
  ;({ default: App } = await server.ssrLoadModule('/src/App.jsx'))
  ;({ default: StatusBar } = await server.ssrLoadModule('/src/components/StatusBar.jsx'))
})

after(async () => {
  await server?.close()
})

const ids = result => result.jobs.map(job => job.job_id)

test('FCFS rows stay fixed as earlier jobs complete', () => {
  const queued = [
    { job_id: 'BLOCK_01', state: 'PENDING', submission_seq: 0, queue_position: 1 },
    { job_id: 'BLOCK_02', state: 'PENDING', submission_seq: 1, queue_position: 2 },
  ]
  const first = stabilizeJobOrder(queued)
  const changed = stabilizeJobOrder([
    { ...queued[0], state: 'COMPLETED', queue_position: undefined },
    { ...queued[1], state: 'ACTIVE', queue_position: undefined },
  ], first.order)
  assert.deepEqual(ids(changed), ['BLOCK_01', 'BLOCK_02'])
})

test('SJF and Priority use authoritative queue positions with deterministic ties', () => {
  const sjf = stabilizeJobOrder([
    { job_id: 'LONG', state: 'PENDING', submission_seq: 0, queue_position: 3 },
    { job_id: 'SHORT_A', state: 'PENDING', submission_seq: 1, queue_position: 1 },
    { job_id: 'SHORT_B', state: 'PENDING', submission_seq: 2, queue_position: 2 },
  ])
  assert.deepEqual(ids(sjf), ['SHORT_A', 'SHORT_B', 'LONG'])

  const priority = stabilizeJobOrder([
    { job_id: 'LOW', state: 'PENDING', submission_seq: 0, queue_position: 2 },
    { job_id: 'HIGH', state: 'PENDING', submission_seq: 1, queue_position: 1 },
  ])
  assert.deepEqual(ids(priority), ['HIGH', 'LOW'])

  const tied = stabilizeJobOrder([
    { job_id: 'FIRST', state: 'PENDING', submission_seq: 3 },
    { job_id: 'SECOND', state: 'PENDING', submission_seq: 4 },
  ])
  assert.deepEqual(ids(tied), ['FIRST', 'SECOND'])
})

test('new queued jobs do not rearrange locked completed rows', () => {
  const result = stabilizeJobOrder([
    { job_id: 'DONE', state: 'COMPLETED', submission_seq: 0 },
    { job_id: 'LONG', state: 'PENDING', submission_seq: 1, queue_position: 2 },
    { job_id: 'SHORT', state: 'PENDING', submission_seq: 2, queue_position: 1 },
  ], ['DONE', 'LONG'])
  assert.deepEqual(ids(result), ['DONE', 'SHORT', 'LONG'])
})

test('Dispatch All feedback distinguishes empty, waiting, and success', () => {
  assert.equal(dispatchFeedback({ pendingJobs: 0 }).message, 'No pending jobs to dispatch.')
  assert.equal(
    dispatchFeedback({ pendingJobs: 2 }).message,
    'Pending jobs are waiting for available resources.',
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

test('execution status displays the idle backend state as NOT RUNNING', () => {
  assert.equal(executionStatusLabel('not_running'), 'NOT RUNNING')
  assert.equal(executionStatusLabel('paused'), 'PAUSED')
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

test('dashboard sections follow the Stage 5 order', () => {
  const html = renderToStaticMarkup(React.createElement(App))
  const labels = [
    'Resource Summary',
    'Operational feedback',
    'Simulation Setup',
    'Job Management',
    'Simulation Execution',
    'Simulation Jobs',
    'Crane Registry',
    'Database Inventory',
  ]
  const positions = labels.map(label => html.indexOf(label))
  assert.ok(positions.every(position => position >= 0))
  assert.deepEqual([...positions].sort((a, b) => a - b), positions)
  assert.match(html, /NOT RUNNING/)
})
