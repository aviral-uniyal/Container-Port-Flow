export const executionStatusLabel = state => String(state).replace(/_/g, ' ').toUpperCase()

export function blockedExecutionFeedback(error) {
  return error
    ? {
        message: `Execution stopped because the backend could not safely continue: ${error}`,
        severity: 'err',
      }
    : {
        message: 'Execution is blocked by unavailable resources or interrupted work.',
        severity: 'warn',
      }
}

const deterministicCompare = (a, b) => {
  const aSequence = Number.isFinite(a.submission_seq) ? a.submission_seq : Number.MAX_SAFE_INTEGER
  const bSequence = Number.isFinite(b.submission_seq) ? b.submission_seq : Number.MAX_SAFE_INTEGER
  if (aSequence !== bSequence) return aSequence - bSequence
  return String(a.job_id).localeCompare(String(b.job_id))
}

// A small, resource-free preview used only when a submission succeeds. At each
// arrival boundary, select the best eligible job; execution remains backend-owned.
export function previewJobOrder(jobs, policy = 'FCFS') {
  const waiting = [...jobs]
  const ordered = []
  let time = 0
  while (waiting.length) {
    const nextArrival = Math.min(...waiting.map(job => job.arrival_time ?? 0))
    time = Math.max(time, nextArrival)
    const eligible = waiting.filter(job => (job.arrival_time ?? 0) <= time)
    eligible.sort((a, b) => {
      if (policy === 'SJF' && a.work_total !== b.work_total)
        return (a.work_total ?? Infinity) - (b.work_total ?? Infinity)
      if (policy === 'PRIORITY' && a.priority !== b.priority)
        return (a.priority ?? Infinity) - (b.priority ?? Infinity)
      if ((a.arrival_time ?? 0) !== (b.arrival_time ?? 0))
        return (a.arrival_time ?? 0) - (b.arrival_time ?? 0)
      return deterministicCompare(a, b)
    })
    const chosen = eligible[0]
    ordered.push(chosen.job_id)
    waiting.splice(waiting.findIndex(job => job.job_id === chosen.job_id), 1)
    time += chosen.work_total ?? 0
  }
  return ordered
}

export function insertJobOrder(order, jobs, newJob, policy = 'FCFS') {
  if (order.includes(newJob.job_id)) return order
  const byId = new Map(jobs.map(job => [job.job_id, job]))
  byId.set(newJob.job_id, newJob)
  const preview = previewJobOrder([...byId.values()], policy)
  const newRank = preview.indexOf(newJob.job_id)
  const ranks = new Map(preview.map((id, index) => [id, index]))
  const index = order.filter(id => (ranks.get(id) ?? Infinity) < newRank).length
  return [...order.slice(0, index), newJob.job_id, ...order.slice(index)]
}

// Snapshot updates only fill in missing IDs. Known IDs retain their positions.
export function stabilizeJobOrder(jobs, previousOrder = []) {
  const byId = new Map(jobs.map(job => [job.job_id, job]))
  const order = [...previousOrder]
  for (const job of jobs) if (!order.includes(job.job_id)) order.push(job.job_id)
  return { order, jobs: order.filter(id => byId.has(id)).map(id => byId.get(id)) }
}

export function stabilizeCraneOrder(cranes, previousOrder = []) {
  const byId = new Map(cranes.map(crane => [crane.crane_id, crane]))
  const order = [...previousOrder]
  for (const crane of cranes) if (!order.includes(crane.crane_id)) order.push(crane.crane_id)
  return { order, cranes: order.filter(id => byId.has(id)).map(id => byId.get(id)) }
}

export function dispatchFeedback({ count = 0, dispatchedJobs = [], pendingJobs = 0, futureJobs = 0 }) {
  if (count > 0) {
    return {
      message: `Dispatched ${count} job${count !== 1 ? 's' : ''}: ${dispatchedJobs.join(', ')}.`,
      severity: 'ok',
    }
  }
  if (pendingJobs > 0 && futureJobs === pendingJobs) {
    return { message: 'Pending jobs are waiting for their arrival time.', severity: 'warn' }
  }
  return pendingJobs > 0
    ? { message: 'Pending jobs are waiting for available resources.', severity: 'warn' }
    : { message: 'No pending jobs to dispatch.', severity: 'warn' }
}

export function stepFeedback(result, snapshotJobs = [], batchMode = false, simulationTime = null) {
  const advanced = result.advanced_jobs ?? []
  const completed = result.completed_jobs ?? []
  const dispatched = result.dispatched_jobs ?? []
  if (completed.length > 0) {
    return { message: `Step completed. Finished: ${completed.join(', ')}.`, severity: 'ok' }
  }
  if (advanced.length > 0) {
    return {
      message: `Step advanced ${advanced.length} active job${advanced.length !== 1 ? 's' : ''}: ${advanced.join(', ')}.`,
      severity: 'ok',
    }
  }
  if (dispatched.length > 0) {
    return {
      message: `Step dispatched ${dispatched.length} job${dispatched.length !== 1 ? 's' : ''}: ${dispatched.join(', ')}.`,
      severity: 'ok',
    }
  }

  const unfinished = snapshotJobs.filter(job =>
    ['PENDING', 'ACTIVE', 'INTERRUPTED'].includes(job.state))
  if (Number.isInteger(simulationTime) && unfinished.length > 0 &&
      unfinished.every(job => job.state === 'PENDING' && job.arrival_time > simulationTime)) {
    return {
      message: `Simulation time advanced to ${simulationTime}; waiting for the next job arrival.`,
      severity: 'ok',
    }
  }
  if (batchMode && unfinished.length > 0 && unfinished.every(job => job.state === 'PENDING')) {
    return {
      message: 'No jobs advanced. Use Dispatch All or Run to start pending jobs in Batch Mode.',
      severity: 'warn',
    }
  }
  if (unfinished.length > 0) {
    return {
      message: 'No jobs advanced because execution is blocked by unavailable resources.',
      severity: 'warn',
    }
  }
  return { message: 'No active jobs to advance.', severity: 'warn' }
}
