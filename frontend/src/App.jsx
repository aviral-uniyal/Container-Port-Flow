import { useState, useEffect, useCallback, useRef } from 'react'
import StatusBar        from './components/StatusBar'
import SimulationSetup  from './components/SimulationSetup'
import ResourceSummary  from './components/ResourceSummary'
import JobForm          from './components/JobForm'
import JobsTable        from './components/JobsTable'
import CraneRegistry    from './components/CraneRegistry'
import DatabaseInventory from './components/DatabaseInventory'
import {
  blockedExecutionFeedback,
  dispatchFeedback,
  executionStatusLabel,
  insertJobOrder,
  stabilizeCraneOrder,
  stabilizeJobOrder,
  stepFeedback,
} from './uiLogic'
import {
  fetchHealth,
  fetchInventory,
  initSimulation,
  submitJob,
  completeJob,
  fetchSnapshot,
  registerCrane,
  failCrane,
  recoverCrane,
  dispatchBatch,
  fetchExecutionStatus,
  runSimulation,
  pauseSimulation,
  stepSimulation,
} from './api'

/**
 * App — root component for Container-Port-Flow dashboard.
 *
 * State held here:
 *   connectionStatus — 'neutral' | 'ok' | 'warn' | 'err'
 *   healthDetail     — { bridge_alive, db_connected } | null
 *   feedback         — { message, severity } | null
 *   resources        — snapshot resources or null
 *   jobs             — array
 *   cranes           — array
 *   loading          — object tracking in-flight requests per action
 */
export default function App() {
  const [connectionStatus, setConnectionStatus] = useState('neutral')
  const [healthDetail,     setHealthDetail]     = useState(null)
  const [feedback,         setFeedback]         = useState(null)
  const [resources,        setResources]        = useState(null)
  const [jobs,             setJobs]             = useState([])
  const [cranes,           setCranes]           = useState([])
  const [loading,          setLoading]          = useState({})
  const [inventory,        setInventory]        = useState(null)
  const [inventoryLoading, setInventoryLoading] = useState(false)
  const [inventoryError,   setInventoryError]   = useState(null)
  const [lastFailResult,   setLastFailResult]   = useState(null)
  // batchMode mirrors the C++ dispatcher's batch_mode flag set during init.
  // When true the dashboard shows the "Dispatch All" button.
  const [batchMode,        setBatchMode]        = useState(false)
  const [execution,        setExecution]        = useState({
    state: 'not_running',
    interval_seconds: 1,
    error: null,
  })
  const snapshotRequestRef = useRef(0)
  const executionRequestRef = useRef(0)
  const executionStateRef = useRef('not_running')
  const runRequestRef = useRef(false)
  const jobOrderRef = useRef([])
  const craneOrderRef = useRef([])
  const jobMetadataRef = useRef(new Map())
  const policyRef = useRef('FCFS')

  /* ── Helper: set loading flag for a given action key ── */
  function setBusy(key, busy) {
    setLoading(prev => ({ ...prev, [key]: busy }))
  }

  /* ── Apply a snapshot response to state ── */
  const applySnapshot = useCallback((data) => {
    const res = data.resources
    if (res) {
      setResources({
        berths: { available: res.avail_berths, total: res.total_berths },
        cranes: { available: res.avail_cranes, total: res.total_cranes },
        trucks: { available: res.avail_trucks, total: res.total_trucks },
      })
    }
    if (Array.isArray(data.jobs)) {
      for (const job of data.jobs) {
        if (!jobMetadataRef.current.has(job.job_id)) jobMetadataRef.current.set(job.job_id, job)
      }
      const stable = stabilizeJobOrder(data.jobs, jobOrderRef.current)
      jobOrderRef.current = stable.order
      setJobs(stable.jobs)
    }
    if (Array.isArray(data.cranes)) {
      const stable = stabilizeCraneOrder(data.cranes.map(c => ({
        crane_id:        c.crane_id,
        status:          c.status,
        assigned_job_id: c.assigned_job || null,
      })), craneOrderRef.current)
      craneOrderRef.current = stable.order
      setCranes(stable.cranes)
    }
    if (data.policy) policyRef.current = data.policy
    if (typeof data.batch_mode === 'boolean') {
      setBatchMode(data.batch_mode)
    }
  }, [])

  /* ── Refresh snapshot from backend ── */
  const refreshSnapshot = useCallback(async () => {
    const requestId = ++snapshotRequestRef.current
    const result = await fetchSnapshot()
    if (result.ok && requestId === snapshotRequestRef.current) {
      applySnapshot(result.data)
    }
    // Silently ignore snapshot errors — the caller decides feedback
    return result
  }, [applySnapshot])

  /* ── Refresh backend-owned execution status ── */
  const refreshExecution = useCallback(async ({ announce = false } = {}) => {
    const requestId = ++executionRequestRef.current
    const result = await fetchExecutionStatus()
    if (!result.ok || requestId !== executionRequestRef.current) return result

    const previousState = executionStateRef.current
    const nextState = result.data.state
    executionStateRef.current = nextState
    setExecution(result.data)

    if (announce && nextState !== previousState) {
      if (nextState === 'completed') {
        setFeedback({ message: 'Automatic execution completed all jobs.', severity: 'ok' })
      } else if (nextState === 'blocked') {
        setFeedback(blockedExecutionFeedback(result.data.error))
      }
    }
    return result
  }, [])

  /* ── Refresh PostgreSQL inventory ── */
  const refreshInventory = useCallback(async ({ silent = false } = {}) => {
    if (!silent) {
      setInventoryLoading(true)
      setInventoryError(null)
    }
    const result = await fetchInventory()
    if (!silent) setInventoryLoading(false)
    if (result.ok && Array.isArray(result.data?.ships)) {
      setInventory(result.data)
    } else {
      setInventory(null)
      setInventoryError(result.error || 'The backend did not return ship records. Check the Stage 1 database migration.')
    }
  }, [])

  /* ── Poll while automatic execution is running ── */
  useEffect(() => {
    if (execution.state !== 'running') return undefined

    let cancelled = false
    let timerId
    async function poll() {
      await refreshSnapshot()
      if (cancelled) return
      const statusResult = await refreshExecution({ announce: true })
      if (cancelled) return
      await refreshInventory({ silent: true })
      if (cancelled) return
      if (statusResult.ok && statusResult.data.state === 'running') {
        timerId = window.setTimeout(poll, 1000)
      } else if (!statusResult.ok) {
        setFeedback({ message: `Execution status refresh failed: ${statusResult.error}`, severity: 'err' })
        timerId = window.setTimeout(poll, 1000)
      }
    }

    timerId = window.setTimeout(poll, 1000)
    return () => {
      cancelled = true
      window.clearTimeout(timerId)
    }
  }, [execution.state, refreshExecution, refreshSnapshot, refreshInventory])

  /* ── Health check on startup ── */
  useEffect(() => {
    async function checkHealth() {
      const result = await fetchHealth()
      if (!result.ok) {
        setConnectionStatus('err')
        setFeedback({ message: `API health check failed: ${result.error}`, severity: 'err' })
        return
      }
      const h = result.data
      setHealthDetail(h)
      if (h.status === 'ready') {
        setConnectionStatus('ok')
        setFeedback({ message: 'Backend connected — API, C++ bridge, and database ready.', severity: 'ok' })
      } else {
        setConnectionStatus('warn')
        const issues = []
        if (!h.bridge_alive) issues.push('C++ bridge down')
        if (!h.db_connected) issues.push('database unavailable')
        setFeedback({
          message: `Backend degraded — ${issues.join(', ')}.`,
          severity: 'warn',
        })
      }
      // Try loading initial snapshot if bridge is alive
      if (h.bridge_alive) {
        await Promise.all([refreshSnapshot(), refreshExecution()])
      }
      // Always attempt inventory; endpoint returns 503 gracefully when DB is down
      await refreshInventory()
    }
    checkHealth()
  }, [refreshSnapshot, refreshInventory, refreshExecution])

  /* ── Initialize simulation ── */
  async function handleInit(formData) {
    setBusy('init', true)
    const result = await initSimulation(formData)
    setBusy('init', false)
    if (!result.ok) {
      setFeedback({ message: `Init failed: ${result.error}`, severity: 'err' })
      return
    }
    snapshotRequestRef.current += 1
    jobOrderRef.current = []
    craneOrderRef.current = []
    jobMetadataRef.current = new Map()
    policyRef.current = result.data.policy || formData.policy || 'FCFS'
    setJobs([])
    setCranes([])
    // Read batch_mode from the C++ response (authoritative source).
    const isBatch = result.data.batch_mode === true
    setBatchMode(isBatch)
    setFeedback({
      message: `Simulation initialized — policy: ${result.data.policy || 'FCFS'}${
        isBatch ? ' · Batch mode ON — click “Dispatch All” to send queued jobs.' : ''
      }.`,
      severity: 'ok',
    })
    setLastFailResult(null)
    await Promise.all([refreshSnapshot(), refreshExecution()])
    await refreshInventory()
  }

  /* ── Automatic execution controls ── */
  async function handleRun() {
    if (executionStateRef.current === 'running' || runRequestRef.current) return
    runRequestRef.current = true
    setBusy('run', true)
    const result = await runSimulation()
    runRequestRef.current = false
    setBusy('run', false)
    if (!result.ok) {
      setFeedback({ message: `Run failed: ${result.error}`, severity: 'err' })
      await Promise.all([refreshSnapshot(), refreshExecution(), refreshInventory()])
      return
    }
    executionRequestRef.current += 1
    executionStateRef.current = result.data.state
    setExecution(result.data)
    setFeedback({ message: 'Automatic execution started or resumed.', severity: 'ok' })
    await refreshSnapshot()
  }

  async function handlePause() {
    setBusy('pause', true)
    const result = await pauseSimulation()
    setBusy('pause', false)
    if (!result.ok) {
      setFeedback({ message: `Pause failed: ${result.error}`, severity: 'err' })
      return
    }
    executionRequestRef.current += 1
    executionStateRef.current = result.data.state
    setExecution(result.data)
    setFeedback({ message: 'Automatic execution paused.', severity: 'ok' })
    await refreshSnapshot()
  }

  async function handleStep() {
    setBusy('step', true)
    const result = await stepSimulation()
    setBusy('step', false)
    if (!result.ok) {
      setFeedback({ message: `Step failed: ${result.error}`, severity: 'err' })
      await Promise.all([refreshSnapshot(), refreshExecution(), refreshInventory()])
      return
    }
    const [snapshotResult] = await Promise.all([refreshSnapshot(), refreshExecution()])
    await refreshInventory()
    const snapshotJobs = snapshotResult.ok ? (snapshotResult.data.jobs ?? []) : jobs
    setFeedback(stepFeedback(result.data, snapshotJobs, batchMode,
      snapshotResult.ok ? snapshotResult.data.simulation_time : null))
  }

  /* ── Dispatch all queued jobs (batch mode only) ── */
  async function handleDispatch() {
    setBusy('dispatch', true)
    const result = await dispatchBatch()
    setBusy('dispatch', false)
    if (!result.ok) {
      setFeedback({ message: `Dispatch failed: ${result.error}`, severity: 'err' })
      await Promise.all([refreshSnapshot(), refreshExecution(), refreshInventory()])
      return
    }
    const count = result.data.count ?? 0
    const dispatchedJobs = result.data.dispatched_jobs ?? []
    const snapshotResult = await refreshSnapshot()
    const pending = (snapshotResult.ok ? snapshotResult.data.jobs ?? [] : jobs)
      .filter(job => job.state === 'PENDING')
    const pendingJobs = pending.length
    const futureJobs = pending.filter(job =>
      Number.isInteger(snapshotResult.data?.simulation_time) &&
      job.arrival_time > snapshotResult.data.simulation_time).length
    setFeedback(dispatchFeedback({ count, dispatchedJobs, pendingJobs, futureJobs }))
    await refreshExecution()
    await refreshInventory()
  }

  /* ── Submit job ── */
  async function handleJobSubmit(formData) {
    setBusy('job', true)
    const result = await submitJob(formData)
    setBusy('job', false)
    if (!result.ok) {
      setFeedback({ message: `Job submit failed: ${result.error}`, severity: 'err' })
      await Promise.all([refreshSnapshot(), refreshExecution(), refreshInventory()])
      return false
    }
    snapshotRequestRef.current += 1
    const newJob = {
      ...formData,
      submission_seq: jobMetadataRef.current.size,
      state: 'PENDING',
      work_completed: 0,
      work_remaining: formData.work_total,
    }
    const nextOrder = insertJobOrder(jobOrderRef.current,
      [...jobMetadataRef.current.values()], newJob, policyRef.current)
    jobOrderRef.current = nextOrder
    jobMetadataRef.current.set(newJob.job_id, newJob)
    setJobs(previous => stabilizeJobOrder([...previous, newJob], nextOrder).jobs)
    setFeedback({
      message: `Job "${formData.job_id}" submitted successfully.`,
      severity: 'ok',
    })
    // The submit itself has succeeded, so let JobForm clear only its ship
    // selection immediately. These independent refreshes can finish afterward.
    void Promise.all([
      refreshSnapshot(),
      refreshExecution({ announce: true }),
      refreshInventory(),
    ])
    return true
  }

  /* ── Complete job ── */
  async function handleJobComplete(jobId) {
    setBusy(`complete-${jobId}`, true)
    const result = await completeJob(jobId)
    setBusy(`complete-${jobId}`, false)
    if (!result.ok) {
      setFeedback({ message: `Complete "${jobId}" failed: ${result.error}`, severity: 'err' })
      await Promise.all([refreshSnapshot(), refreshExecution(), refreshInventory()])
      return
    }
    setFeedback({
      message: `Job "${jobId}" marked completed.`,
      severity: 'ok',
    })
    await refreshSnapshot()
    await refreshInventory()
  }

  /* ── Register crane ── */
  async function handleCraneRegister(craneId) {
    setBusy('crane', true)
    const result = await registerCrane(craneId)
    setBusy('crane', false)
    if (!result.ok) {
      setFeedback({ message: `Crane registration failed: ${result.error}`, severity: 'err' })
      await Promise.all([refreshSnapshot(), refreshExecution(), refreshInventory()])
      return
    }
    snapshotRequestRef.current += 1
    if (!craneOrderRef.current.includes(craneId)) {
      craneOrderRef.current = [...craneOrderRef.current, craneId]
    }
    setCranes(previous => stabilizeCraneOrder([
      ...previous, { crane_id: craneId, status: 'AVAILABLE', assigned_job_id: null },
    ], craneOrderRef.current).cranes)
    setFeedback({
      message: `Crane "${craneId}" registered successfully.`,
      severity: 'ok',
    })
    await refreshSnapshot()
    // Inventory reflects the new crane's persisted status
    await refreshInventory()
  }

  /* ── Fail crane ── */
  async function handleCraneFail(craneId) {
    setBusy(`fail-${craneId}`, true)
    const result = await failCrane(craneId)
    setBusy(`fail-${craneId}`, false)
    if (!result.ok) {
      setFeedback({ message: `Failed to mark crane "${craneId}" as failed: ${result.error}`, severity: 'err' })
      await Promise.all([refreshSnapshot(), refreshExecution(), refreshInventory()])
      return
    }
    const data = result.data
    setLastFailResult(data)
    let msg = `Crane "${craneId}" marked as FAILED.`
    if (data.affected_job_id) {
      msg += ` Job "${data.affected_job_id}" was affected.`
      if (data.job_interrupted) {
        msg += ' Job has been interrupted and re-queued.'
      }
    }
    if (data.db_sync_warning) {
      msg += ` ⚠ DB sync issue: ${data.db_sync_warning}`
      setFeedback({ message: msg, severity: 'warn' })
    } else {
      setFeedback({ message: msg, severity: 'ok' })
    }
    await refreshSnapshot()
    await refreshExecution({ announce: true })
    await refreshInventory()
  }

  /* ── Recover crane ── */
  async function handleCraneRecover(craneId) {
    setBusy(`recover-${craneId}`, true)
    const result = await recoverCrane(craneId)
    setBusy(`recover-${craneId}`, false)
    if (!result.ok) {
      if (result.partial?.runtime_changed) setLastFailResult(null)
      setFeedback({ message: `Crane "${craneId}": ${result.error}`, severity: 'err' })
      await Promise.all([refreshSnapshot(), refreshExecution(), refreshInventory()])
      return
    }
    const data = result.data
    setLastFailResult(null)
    let msg = `Crane "${craneId}" recovered to ${data.status}.`
    if (data.dispatched_job_id) {
      msg += ` Job "${data.dispatched_job_id}" was dispatched.`
    }
    if (data.db_sync_warning) {
      msg += ` ⚠ DB sync issue: ${data.db_sync_warning}`
      setFeedback({ message: msg, severity: 'warn' })
    } else {
      setFeedback({ message: msg, severity: 'ok' })
    }
    await refreshSnapshot()
    await refreshExecution({ announce: true })
    await refreshInventory()
  }

  /* ── Manual snapshot refresh ── */
  async function handleRefresh() {
    setBusy('refresh', true)
    const result = await refreshSnapshot()
    // Also refresh DB inventory on manual refresh
    await refreshInventory()
    setBusy('refresh', false)
    if (!result.ok) {
      setFeedback({ message: `Snapshot refresh failed: ${result.error}`, severity: 'err' })
    } else {
      setFeedback({ message: 'Snapshot refreshed.', severity: 'ok' })
    }
  }

  return (
    <>
      {/* ── Header ──────────────────────────────────────────── */}
      <header className="app-header" role="banner">
        <h1 className="app-header__logo">Container-Port-Flow</h1>
        <span className="app-header__subtitle">Port Operations Dashboard</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 'var(--space-2)' }}>
          <button
            id="btn-refresh-snapshot"
            className="btn btn--outline btn--sm"
            onClick={handleRefresh}
            disabled={loading.refresh}
          >
            {loading.refresh ? 'Refreshing…' : '↻ Refresh'}
          </button>
        </div>
      </header>

      {/* ── Main ────────────────────────────────────────────── */}
      <main className="app-main" role="main">

        {/* Status Bar */}
        <StatusBar
          connectionStatus={connectionStatus}
          healthDetail={healthDetail}
        />

        {/* Resource Summary — full width */}
        <ResourceSummary resources={resources} />

        <section
          className={`operational-feedback operational-feedback--${feedback?.severity ?? 'neutral'}`}
          role="status"
          aria-live="polite"
        >
          <span className="operational-feedback__label">Operational feedback</span>
          <span>{feedback?.message ?? 'No operations performed yet.'}</span>
        </section>

        <div className="app-main__two-col">
          <SimulationSetup onSubmit={handleInit} disabled={loading.init} />
          <JobForm
            onSubmit={handleJobSubmit}
            disabled={loading.job}
            ships={inventory?.ships}
            inventoryLoading={inventoryLoading}
            inventoryError={inventoryError}
          />
        </div>

        {/* Execution controls — backend status is authoritative */}
        <section className="card execution-controls" aria-labelledby="execution-controls-title">
          <div className="card__header execution-controls__header">
            <h2 className="card__title" id="execution-controls-title">Simulation Execution</h2>
            <span className={`execution-status execution-status--${execution.state}`} role="status">
              {executionStatusLabel(execution.state)}
            </span>
          </div>
          <div className="card__body execution-controls__body">
            <div className="execution-controls__actions">
              <button
                id="btn-run-simulation"
                className="btn btn--primary"
                onClick={handleRun}
                disabled={execution.state === 'running' || loading.run || loading.pause || loading.step}
              >
                {loading.run ? 'Starting…' : '▶ Run'}
              </button>
              <button
                id="btn-pause-simulation"
                className="btn btn--outline"
                onClick={handlePause}
                disabled={execution.state !== 'running' || loading.pause}
              >
                {loading.pause ? 'Pausing…' : 'Ⅱ Pause'}
              </button>
              <button
                id="btn-step-simulation"
                className="btn btn--outline"
                onClick={handleStep}
                disabled={execution.state === 'running' || loading.step || loading.run || loading.pause}
              >
                {loading.step ? 'Stepping…' : 'Step +1'}
              </button>
            </div>
            <span className="execution-controls__hint">
              Work progress comes from the C++ dispatcher. Run advances about once per second.
            </span>
          </div>
        </section>

        {/* Jobs Table — full width */}
        <JobsTable
          jobs={jobs}
          onComplete={handleJobComplete}
          onDispatch={handleDispatch}
          batchMode={batchMode}
          loading={loading}
        />

        {/* Crane Registry — full width */}
        <CraneRegistry
          cranes={cranes}
          capacity={resources?.cranes.total}
          inventory={inventory}
          onRegister={handleCraneRegister}
          onFail={handleCraneFail}
          onRecover={handleCraneRecover}
          disabled={loading.crane}
          loading={loading}
          lastFailResult={lastFailResult}
        />

        {/* Database Inventory — PostgreSQL physical resources */}
        <DatabaseInventory
          inventory={inventory}
          loading={inventoryLoading}
          error={inventoryError}
        />

      </main>
    </>
  )
}
