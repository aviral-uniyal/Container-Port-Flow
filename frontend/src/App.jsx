import { useState, useEffect, useCallback } from 'react'
import StatusBar        from './components/StatusBar'
import SimulationSetup  from './components/SimulationSetup'
import ResourceSummary  from './components/ResourceSummary'
import JobForm          from './components/JobForm'
import JobsTable        from './components/JobsTable'
import CraneRegistry    from './components/CraneRegistry'
import DatabaseInventory from './components/DatabaseInventory'
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
  reconcileState,
  dispatchBatch,
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
      setJobs(data.jobs)
    }
    if (Array.isArray(data.cranes)) {
      setCranes(data.cranes.map(c => ({
        crane_id:        c.crane_id,
        status:          c.status,
        assigned_job_id: c.assigned_job || null,
      })))
    }
  }, [])

  /* ── Refresh snapshot from backend ── */
  const refreshSnapshot = useCallback(async () => {
    const result = await fetchSnapshot()
    if (result.ok) {
      applySnapshot(result.data)
    }
    // Silently ignore snapshot errors — the caller decides feedback
    return result
  }, [applySnapshot])

  /* ── Refresh PostgreSQL inventory ── */
  const refreshInventory = useCallback(async () => {
    setInventoryLoading(true)
    setInventoryError(null)
    const result = await fetchInventory()
    setInventoryLoading(false)
    if (result.ok) {
      setInventory(result.data)
    } else {
      setInventoryError(result.error)
    }
  }, [])

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
        await refreshSnapshot()
      }
      // Always attempt inventory; endpoint returns 503 gracefully when DB is down
      await refreshInventory()
    }
    checkHealth()
  }, [refreshSnapshot, refreshInventory])

  /* ── Initialize simulation ── */
  async function handleInit(formData) {
    setBusy('init', true)
    const result = await initSimulation(formData)
    setBusy('init', false)
    if (!result.ok) {
      setFeedback({ message: `Init failed: ${result.error}`, severity: 'err' })
      return
    }
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
    await refreshSnapshot()
    await refreshInventory()
  }

  /* ── Dispatch all queued jobs (batch mode only) ── */
  async function handleDispatch() {
    setBusy('dispatch', true)
    const result = await dispatchBatch()
    setBusy('dispatch', false)
    if (!result.ok) {
      setFeedback({ message: `Dispatch failed: ${result.error}`, severity: 'err' })
      return
    }
    const count = result.data.count ?? 0
    const ids   = (result.data.dispatched_jobs ?? []).join(', ')
    setFeedback({
      message: count > 0
        ? `Dispatched ${count} job${count !== 1 ? 's' : ''}: ${ids}.`
        : 'Dispatch All ran — no eligible jobs were waiting.',
      severity: count > 0 ? 'ok' : 'warn',
    })
    await refreshSnapshot()
    await refreshInventory()
  }

  /* ── Submit job ── */
  async function handleJobSubmit(formData) {
    setBusy('job', true)
    const result = await submitJob(formData)
    setBusy('job', false)
    if (!result.ok) {
      setFeedback({ message: `Job submit failed: ${result.error}`, severity: 'err' })
      return
    }
    setFeedback({
      message: `Job "${formData.job_id}" submitted successfully.`,
      severity: 'ok',
    })
    await refreshSnapshot()
    await refreshInventory()
  }

  /* ── Complete job ── */
  async function handleJobComplete(jobId) {
    setBusy(`complete-${jobId}`, true)
    const result = await completeJob(jobId)
    setBusy(`complete-${jobId}`, false)
    if (!result.ok) {
      setFeedback({ message: `Complete "${jobId}" failed: ${result.error}`, severity: 'err' })
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
      return
    }
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
    await refreshInventory()
  }

  /* ── Recover crane ── */
  async function handleCraneRecover(craneId) {
    setBusy(`recover-${craneId}`, true)
    const result = await recoverCrane(craneId)
    setBusy(`recover-${craneId}`, false)
    if (!result.ok) {
      setFeedback({ message: `Failed to recover crane "${craneId}": ${result.error}`, severity: 'err' })
      return
    }
    const data = result.data
    setLastFailResult(null)
    let msg = `Crane "${craneId}" recovered to AVAILABLE.`
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
    await refreshInventory()
  }

  /* ── Reconcile state between C++ snapshot and PostgreSQL ── */
  async function handleReconcile() {
    setBusy('reconcile', true)
    const result = await reconcileState()
    setBusy('reconcile', false)
    if (!result.ok) {
      setFeedback({ message: `Reconciliation failed: ${result.error}`, severity: 'err' })
    } else {
      setFeedback({
        message: `Reconciliation complete: ${result.data.reconciled_cranes} cranes, ${result.data.reconciled_jobs} jobs synced.`,
        severity: 'ok',
      })
    }
    await refreshSnapshot()
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
            id="btn-reconcile-state"
            className="btn btn--outline btn--sm"
            onClick={handleReconcile}
            disabled={loading.reconcile}
            title="Synchronize database records with authoritative runtime snapshot"
          >
            {loading.reconcile ? 'Syncing…' : '⚖ Reconcile'}
          </button>
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
          message={feedback?.message}
          messageSeverity={feedback?.severity}
        />

        {/* Resource Summary — full width */}
        <ResourceSummary resources={resources} />

        {/* Two-column: Setup left, Job form right */}
        <div className="app-main__two-col">
          <SimulationSetup onSubmit={handleInit} disabled={loading.init} />
          <JobForm onSubmit={handleJobSubmit} disabled={loading.job} />
        </div>

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
          onRegister={handleCraneRegister}
          onFail={handleCraneFail}
          onRecover={handleCraneRecover}
          onReconcile={handleReconcile}
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
