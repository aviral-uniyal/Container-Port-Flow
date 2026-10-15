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
    setFeedback({
      message: `Simulation initialized — policy: ${result.data.policy || 'FCFS'}.`,
      severity: 'ok',
    })
    await refreshSnapshot()
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
        <button
          id="btn-refresh-snapshot"
          className="btn btn--outline btn--sm"
          style={{ marginLeft: 'auto' }}
          onClick={handleRefresh}
          disabled={loading.refresh}
        >
          {loading.refresh ? 'Refreshing…' : '↻ Refresh'}
        </button>
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
        <JobsTable jobs={jobs} onComplete={handleJobComplete} loading={loading} />

        {/* Crane Registry — full width */}
        <CraneRegistry
          cranes={cranes}
          onRegister={handleCraneRegister}
          disabled={loading.crane}
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
