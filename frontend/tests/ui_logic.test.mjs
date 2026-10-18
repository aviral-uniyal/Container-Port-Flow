import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { dispatchBatch } from '../src/api.js'

let server
let App
let JobsTable
let StatusBar
let dispatchFeedback
let blockedExecutionFeedback
let executionStatusLabel
let stabilizeJobOrder
let stepFeedback

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ blockedExecutionFeedback, dispatchFeedback, executionStatusLabel,
    stabilizeJobOrder, stepFeedback } =
    await server.ssrLoadModule('/src/uiLogic.js'))
  ;({ default: App } = await server.ssrLoadModule('/src/App.jsx'))
  ;({ default: JobsTable } = await server.ssrLoadModule('/src/components/JobsTable.jsx'))
  ;({ default: StatusBar } = await server.ssrLoadModule('/src/components/StatusBar.jsx'))
})

after(async () => {
  await server?.close()
})

const ids = result => result.jobs.map(job => job.job_id)

test('FCFS, SJF, and Priority retain authoritative dispatch order through completion', () => {
  for (const { first, second } of [
    { first: 'FCFS_001', second: 'FCFS_002' },
    { first: 'SJF_002', second: 'SJF_001' },
    { first: 'PRIORITY_002', second: 'PRIORITY_001' },
  ]) {
    const queued = [
      { job_id: second, state: 'PENDING', submission_seq: 0, queue_position: 2 },
      { job_id: first, state: 'PENDING', submission_seq: 1, queue_position: 1 },
    ]
    assert.deepEqual(ids(stabilizeJobOrder(queued)), [first, second])

    const firstRunning = queued.map(job => job.job_id === first
      ? { ...job, state: 'ACTIVE', queue_position: undefined, dispatch_sequence: 0 }
      : job)
    assert.deepEqual(ids(stabilizeJobOrder(firstRunning)), [first, second])

    const bothFinished = firstRunning.map(job => job.job_id === first
      ? { ...job, state: 'COMPLETED' }
      : { ...job, state: 'COMPLETED', queue_position: undefined, dispatch_sequence: 1 })
    assert.deepEqual(ids(stabilizeJobOrder(bothFinished)), [first, second])
    assert.deepEqual(ids(stabilizeJobOrder([...bothFinished].reverse())), [first, second])
  }
})

test('failure, interruption, and recovery do not move a dispatched job', () => {
  for (const state of ['ACTIVE', 'INTERRUPTED', 'ACTIVE', 'COMPLETED']) {
    const jobs = [
      { job_id: 'WAITING', state: 'PENDING', submission_seq: 0, queue_position: 1 },
      { job_id: 'FIRST', state, submission_seq: 1, dispatch_sequence: 0 },
    ]
    assert.deepEqual(ids(stabilizeJobOrder(jobs)), ['FIRST', 'WAITING'])
  }
})

test('concurrent dispatch and reload ordering are deterministic', () => {
  const snapshot = [
    { job_id: 'THIRD', state: 'ACTIVE', submission_seq: 0, dispatch_sequence: 2 },
    { job_id: 'FIRST', state: 'ACTIVE', submission_seq: 2, dispatch_sequence: 0 },
    { job_id: 'SECOND', state: 'ACTIVE', submission_seq: 1, dispatch_sequence: 1 },
  ]
  const expected = ['FIRST', 'SECOND', 'THIRD']
  assert.deepEqual(ids(stabilizeJobOrder(snapshot)), expected)
  assert.deepEqual(ids(stabilizeJobOrder([...snapshot].reverse())), expected)
})

test('later submissions never rewrite established execution history', () => {
  const result = stabilizeJobOrder([
    { job_id: 'COMPLETED_FIRST', state: 'COMPLETED', submission_seq: 0, dispatch_sequence: 0 },
    { job_id: 'LATER_HIGH', state: 'PENDING', submission_seq: 2, queue_position: 1 },
    { job_id: 'EARLIER_LOW', state: 'PENDING', submission_seq: 1, queue_position: 2 },
  ])
  assert.deepEqual(ids(result), ['COMPLETED_FIRST', 'LATER_HIGH', 'EARLIER_LOW'])
})

test('missing and malformed queue positions use deterministic fallbacks', () => {
  const result = stabilizeJobOrder([
    { job_id: 'STRING_POSITION', state: 'PENDING', submission_seq: 3, queue_position: '1' },
    { job_id: 'NEGATIVE_POSITION', state: 'PENDING', submission_seq: 2, queue_position: -1 },
    { job_id: 'NO_POSITION', state: 'PENDING', submission_seq: 1 },
    { job_id: 'VALID_POSITION', state: 'PENDING', submission_seq: 9, queue_position: 2 },
  ])
  assert.deepEqual(ids(result), [
    'VALID_POSITION', 'NO_POSITION', 'NEGATIVE_POSITION', 'STRING_POSITION',
  ])
})

test('Jobs table explains scheduling and only queued jobs show queue positions', () => {
  const ordered = stabilizeJobOrder([
    { job_id: 'DONE', state: 'COMPLETED', submission_seq: 0, priority: 3, dispatch_sequence: 3 },
    { job_id: 'WAITING', state: 'PENDING', submission_seq: 1, priority: 1, queue_position: 1 },
    { job_id: 'RUNNING', state: 'ACTIVE', submission_seq: 2, priority: 2, crane_fulfilled: true, dispatch_sequence: 0 },
    { job_id: 'DAMAGED', state: 'ACTIVE', submission_seq: 3, priority: 3, crane_fulfilled: false, dispatch_sequence: 1 },
    { job_id: 'STOPPED', state: 'INTERRUPTED', submission_seq: 4, priority: 4, crane_fulfilled: false, dispatch_sequence: 2 },
  ]).jobs
  const html = renderToStaticMarkup(React.createElement(JobsTable, { jobs: ordered }))
  assert.match(html, /Queue Position/)
  assert.match(html, /Crane Supply/)
  assert.match(html, /Queued jobs follow the selected scheduling policy/)
  assert.match(html, /Rows retain first-dispatch order/)
  assert.match(html, /State and crane supply are shown separately/)
  assert.match(html, /FULLY SUPPLIED/)
  assert.equal((html.match(/UNDER-SUPPLIED/g) ?? []).length, 2)
  assert.ok(html.indexOf('RUNNING') < html.indexOf('DONE'))
  assert.ok(html.indexOf('DONE') < html.indexOf('WAITING'))
  assert.match(html, /WAITING[\s\S]*?<td>1<\/td>/)
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
