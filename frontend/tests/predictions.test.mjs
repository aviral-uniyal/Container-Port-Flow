import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { after, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { requestBerthDuration } from '../src/api.js'

const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
const { default: App } = await server.ssrLoadModule('/src/App.jsx')
const { default: Predictions, PredictionResult, predictionOutcome } =
  await server.ssrLoadModule('/src/components/Predictions.jsx')

after(async () => { await server?.close() })

test('all three views stay mounted and navigation targets each view', async () => {
  const html = renderToStaticMarkup(React.createElement(App))
  const source = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
  for (const [name, id] of [['Dashboard', 'dashboard-view'], ['Inventory', 'inventory-view'], ['Predictions', 'predictions-view']]) {
    assert.match(html, new RegExp(`aria-controls="${id}"[^>]*>${name}</button>`))
    assert.match(html, new RegExp(`id="${id}"`))
  }
  assert.match(html, /id="inventory-view"[^>]*hidden=""/)
  assert.match(html, /id="predictions-view"[^>]*hidden=""/)
  assert.match(source, /onClick=\{\(\) => setActiveTab\('predictions'\)\}/)
  assert.match(source, /hidden=\{activeTab !== 'predictions'\}/)
  assert.match(source, /hidden=\{activeTab !== 'dashboard'\}/)
  assert.match(source, /hidden=\{activeTab !== 'inventory'\}/)
})

test('prediction form starts idle and requires an explicit submit', () => {
  const html = renderToStaticMarkup(React.createElement(Predictions, {
    ships: [{ vessel_name: 'ALS APOLLO' }],
  }))
  assert.match(html, /Generate Prediction/)
  assert.match(html, /No prediction requested/)
  assert.match(html, /value="ALS APOLLO"/)
  assert.match(html, /actual berthing/)
  assert.match(html, /does not affect the existing scheduler/)
})

test('prediction API sends only a read-only estimate request', async () => {
  const viteConfig = await readFile(new URL('../vite.config.js', import.meta.url), 'utf8')
  assert.match(viteConfig, /'\/predictions':\s*\{\s*target: 'http:\/\/127\.0\.0\.1:8000'/)
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => ({ status: 'unavailable', reason: 'unseen_vessel' }) }
  }
  try {
    const result = await requestBerthDuration('ALS APOLLO')
    assert.equal(result.ok, true)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, '/predictions/berth-duration')
    assert.equal(calls[0].options.method, 'POST')
    assert.deepEqual(JSON.parse(calls[0].options.body), { vessel_name: 'ALS APOLLO' })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('available estimate shows duration, provenance, timestamp, and advisory label', () => {
  const outcome = predictionOutcome({ ok: true, data: {
    designation: 'experimental_advisory', status: 'available', vessel_name: 'ALS APOLLO',
    estimated_duration_hours: 20.589, units: 'hours', model_version: 'vessel_forest:abc',
    training_period: '2019-01-01 through 2020-12-31', predicted_at: '2026-11-14T00:00:00+00:00',
    prediction_moment: 'actual berthing', explanation: 'Historical vessel-based estimate.',
  } })
  assert.equal(outcome.state, 'available')
  const html = renderToStaticMarkup(React.createElement(PredictionResult, { outcome }))
  for (const value of ['ALS APOLLO', '20.59', 'hours', 'Experimental advisory',
    'vessel_forest:abc', '2019-01-01 through 2020-12-31',
    '2026-11-14T00:00:00+00:00', 'actual berthing', 'Historical vessel-based estimate.']) {
    assert.ok(html.includes(value), `Expected ${value}`)
  }
})

test('unseen vessels and missing artifacts never display an estimate', () => {
  for (const reason of ['unseen_vessel', 'missing_model_artifact', 'model_load_failed', 'prediction_failed']) {
    const outcome = predictionOutcome({ ok: true, data: {
      status: 'unavailable', reason, estimated_duration_hours: null,
    } })
    assert.equal(outcome.state, 'unavailable')
    const html = renderToStaticMarkup(React.createElement(PredictionResult, { outcome }))
    assert.match(html, /Prediction unavailable/)
    assert.doesNotMatch(html, /hours<\/span>/)
  }
  assert.equal(predictionOutcome({ ok: true, data: { status: 'available', estimated_duration_hours: null } }).state, 'error')
})

test('HTTP validation errors and connection errors are shown as errors', async () => {
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = async () => ({
      ok: false, status: 422,
      json: async () => ({ detail: [{ loc: ['body', 'vessel_name'], msg: 'Field required' }] }),
    })
    const badRequest = await requestBerthDuration('')
    assert.equal(badRequest.ok, false)
    assert.match(predictionOutcome(badRequest).message, /vessel_name: Field required/)
    globalThis.fetch = async () => { throw new Error('offline') }
    const offline = await requestBerthDuration('ALS APOLLO')
    const html = renderToStaticMarkup(React.createElement(PredictionResult, { outcome: predictionOutcome(offline) }))
    assert.match(html, /Prediction request error/)
    assert.match(html, /API unreachable/)
  } finally {
    globalThis.fetch = originalFetch
  }
})
