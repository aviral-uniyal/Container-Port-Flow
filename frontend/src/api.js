/**
 * api.js — thin fetch wrappers for the Container-Port-Flow backend.
 *
 * All paths are relative (e.g. "/health") so they hit the Vite dev-proxy in
 * development and the same-origin backend in production.
 *
 * Every function returns { ok, data } on success or { ok, error } on failure.
 * Callers never need to handle raw Response objects or try/catch around fetch.
 */

/**
 * Parse an API response. Handles JSON bodies, FastAPI 422 validation errors,
 * plain error responses, and network failures.
 */
async function handleResponse(res) {
  let body
  try {
    body = await res.json()
  } catch {
    // Non-JSON response (e.g. 502 from a dead proxy)
    if (!res.ok) {
      return { ok: false, error: `Server error (${res.status})` }
    }
    return { ok: false, error: 'Unexpected non-JSON response' }
  }

  if (!res.ok) {
    // FastAPI validation error (422)
    if (res.status === 422 && Array.isArray(body.detail)) {
      const messages = body.detail.map(e => {
        const loc = (e.loc || []).slice(1).join(' → ') || 'unknown field'
        return `${loc}: ${e.msg}`
      })
      return { ok: false, error: `Validation error — ${messages.join('; ')}` }
    }
    // Standard FastAPI error
    const detail = body.detail || body.error || JSON.stringify(body)
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      return { ok: false, error: detail.message || JSON.stringify(detail), partial: detail }
    }
    return { ok: false, error: detail }
  }

  return { ok: true, data: body }
}

/**
 * GET /health — check API, bridge, and database status.
 */
export async function fetchHealth() {
  try {
    const res = await fetch('/health')
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * POST /simulation/init
 */
export async function initSimulation(payload) {
  try {
    const res = await fetch('/simulation/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * POST /simulation/cranes
 */
export async function registerCrane(craneId) {
  try {
    const res = await fetch('/simulation/cranes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ crane_id: craneId }),
    })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * POST /simulation/jobs
 */
export async function submitJob(payload) {
  try {
    const res = await fetch('/simulation/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ship_id:      Number(payload.ship_id),
        job_id:       String(payload.job_id),
        arrival_time: Number(payload.arrival_time),
        work_total:   Number(payload.work_total),
        priority:     Number(payload.priority),
        berths:       Number(payload.berths),
        cranes:       Number(payload.cranes),
        trucks:       Number(payload.trucks),
      }),
    })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * POST /simulation/jobs/{jobId}/complete
 */
export async function completeJob(jobId) {
  try {
    const res = await fetch(`/simulation/jobs/${encodeURIComponent(jobId)}/complete`, {
      method: 'POST',
    })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * GET /simulation/snapshot
 */
export async function fetchSnapshot() {
  try {
    const res = await fetch('/simulation/snapshot')
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/** GET /simulation/execution — read the backend runner state. */
export async function fetchExecutionStatus() {
  try {
    const res = await fetch('/simulation/execution')
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/** POST /simulation/run — start or resume automatic execution. */
export async function runSimulation() {
  try {
    const res = await fetch('/simulation/run', { method: 'POST' })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/** POST /simulation/pause — stop automatic execution safely. */
export async function pauseSimulation() {
  try {
    const res = await fetch('/simulation/pause', { method: 'POST' })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/** POST /simulation/step — advance once without batch continuation. */
export async function stepSimulation() {
  try {
    const res = await fetch('/simulation/step', { method: 'POST' })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * GET /inventory — PostgreSQL-backed physical resource inventory.
 * Returns { terminals, berths, cranes, ships } from the database.
 * Independent of the C++ simulation runtime state.
 */
export async function fetchInventory() {
  try {
    const res = await fetch('/inventory')
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * POST /simulation/cranes/{craneId}/fail — mark a crane as FAILED.
 */
export async function failCrane(craneId) {
  try {
    const res = await fetch(`/simulation/cranes/${encodeURIComponent(craneId)}/fail`, {
      method: 'POST',
    })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * POST /simulation/cranes/{craneId}/recover — recover a FAILED crane.
 */
export async function recoverCrane(craneId) {
  try {
    const res = await fetch(`/simulation/cranes/${encodeURIComponent(craneId)}/recover`, {
      method: 'POST',
    })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * POST /simulation/reconcile — reconcile PostgreSQL state with C++ runtime snapshot.
 */
export async function reconcileState() {
  try {
    const res = await fetch('/simulation/reconcile', {
      method: 'POST',
    })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}

/**
 * POST /simulation/dispatch — flush the pending job queue using the active
 * scheduling policy.  Only meaningful in batch mode.  Returns
 * { ok, count, dispatched_jobs: [...] }.
 */
export async function dispatchBatch() {
  try {
    const res = await fetch('/simulation/dispatch', {
      method: 'POST',
    })
    return handleResponse(res)
  } catch {
    return { ok: false, error: 'API unreachable' }
  }
}
