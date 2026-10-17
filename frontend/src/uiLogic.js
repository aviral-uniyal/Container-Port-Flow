const isQueued = job => job.state === 'PENDING' || job.state === 'INTERRUPTED'

export const executionStatusLabel = state => String(state).replace(/_/g, ' ').toUpperCase()

const queueCompare = (a, b) => {
  const aPosition = Number.isFinite(a.queue_position) ? a.queue_position : Number.MAX_SAFE_INTEGER
  const bPosition = Number.isFinite(b.queue_position) ? b.queue_position : Number.MAX_SAFE_INTEGER
  if (aPosition !== bPosition) return aPosition - bPosition
  const sequenceDifference = (a.submission_seq ?? Number.MAX_SAFE_INTEGER) -
    (b.submission_seq ?? Number.MAX_SAFE_INTEGER)
  if (sequenceDifference !== 0) return sequenceDifference
  return String(a.job_id).localeCompare(String(b.job_id))
}

/**
 * Keep established row positions stable while allowing the authoritative C++
 * queue order to rearrange only jobs that are still queued. Locked rows act as
 * anchors, so ACTIVE -> COMPLETED transitions never make another job jump.
 */
export function stabilizeJobOrder(jobs, previousOrder = []) {
  const byId = new Map(jobs.map(job => [job.job_id, job]))
  const order = previousOrder.filter(id => byId.has(id))
  const known = new Set(order)
  const unseen = jobs
    .filter(job => !known.has(job.job_id))
    .sort((a, b) => (a.submission_seq ?? Number.MAX_SAFE_INTEGER) -
      (b.submission_seq ?? Number.MAX_SAFE_INTEGER) ||
      String(a.job_id).localeCompare(String(b.job_id)))
  order.push(...unseen.map(job => job.job_id))

  const queuedSlots = []
  const queuedJobs = []
  order.forEach((id, index) => {
    const job = byId.get(id)
    if (job && isQueued(job)) {
      queuedSlots.push(index)
      queuedJobs.push(job)
    }
  })
  queuedJobs.sort(queueCompare)
  queuedSlots.forEach((slot, index) => {
    order[slot] = queuedJobs[index].job_id
  })

  return { order, jobs: order.map(id => byId.get(id)) }
}

export function dispatchFeedback({ count = 0, dispatchedJobs = [], pendingJobs = 0 }) {
  if (count > 0) {
    return {
      message: `Dispatched ${count} job${count !== 1 ? 's' : ''}: ${dispatchedJobs.join(', ')}.`,
      severity: 'ok',
    }
  }
  return pendingJobs > 0
    ? { message: 'Pending jobs are waiting for available resources.', severity: 'warn' }
    : { message: 'No pending jobs to dispatch.', severity: 'warn' }
}

export function stepFeedback(result, snapshotJobs = [], batchMode = false) {
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
