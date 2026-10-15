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
