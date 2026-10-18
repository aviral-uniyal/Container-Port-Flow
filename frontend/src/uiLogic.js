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

const queueCompare = (a, b) => {
  const aPosition = Number.isInteger(a.queue_position) && a.queue_position > 0
    ? a.queue_position
    : Number.MAX_SAFE_INTEGER
  const bPosition = Number.isInteger(b.queue_position) && b.queue_position > 0
    ? b.queue_position
    : Number.MAX_SAFE_INTEGER
  if (aPosition !== bPosition) return aPosition - bPosition
  return deterministicCompare(a, b)
}

/**
 * Present dispatched jobs in their immutable C++ first-dispatch order, then
 * jobs that have not run yet in the live C++ queue order. Lifecycle state is
 * deliberately excluded, so execution, failure, recovery, and completion do
 * not move an established row. The result depends only on snapshot metadata,
 * making browser reloads deterministic without a frontend-only cache.
 */
export function stabilizeJobOrder(jobs, _previousOrder = []) {
  const orderedJobs = [...jobs].sort((a, b) => {
    const aDispatched = Number.isInteger(a.dispatch_sequence) && a.dispatch_sequence >= 0
    const bDispatched = Number.isInteger(b.dispatch_sequence) && b.dispatch_sequence >= 0
    if (aDispatched && bDispatched) {
      if (a.dispatch_sequence !== b.dispatch_sequence) {
        return a.dispatch_sequence - b.dispatch_sequence
      }
      return deterministicCompare(a, b)
    }
    if (aDispatched !== bDispatched) return aDispatched ? -1 : 1
    return queueCompare(a, b)
  })

  return { order: orderedJobs.map(job => job.job_id), jobs: orderedJobs }
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
