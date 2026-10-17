/**
 * JobsTable — shows submitted simulation jobs.
 * Columns: job ID, state, work completed, work remaining, actions.
 *
 * Props:
 *   jobs — array of job objects. Empty array shows an empty-state row.
 *   onComplete — callback(job_id) for the Complete action button.
 */
export default function JobsTable({ jobs = [], onComplete, loading = {} }) {
  const STATE_BADGE = {
    PENDING:   'badge badge--pending',
    ACTIVE:    'badge badge--active',
    COMPLETED: 'badge badge--completed',
    CANCELLED: 'badge badge--failed',
    FAILED:    'badge badge--failed',
  }

  // Keep running work separate from the dispatch queue, then show queued jobs
  // in the exact order calculated by PortDispatcher.  submission_seq provides
  // deterministic ordering for non-queued jobs and compatibility fallback.
  const stateGroup = state => {
    if (state === 'ACTIVE') return 0
    if (state === 'PENDING' || state === 'INTERRUPTED') return 1
    return 2
  }
  const displayJobs = [...jobs].sort((a, b) => {
    const groupDifference = stateGroup(a.state) - stateGroup(b.state)
    if (groupDifference !== 0) return groupDifference

    const aQueued = a.state === 'PENDING' || a.state === 'INTERRUPTED'
    const bQueued = b.state === 'PENDING' || b.state === 'INTERRUPTED'
    if (aQueued && bQueued) {
      const aPosition = Number.isFinite(a.queue_position) ? a.queue_position : Number.MAX_SAFE_INTEGER
      const bPosition = Number.isFinite(b.queue_position) ? b.queue_position : Number.MAX_SAFE_INTEGER
      if (aPosition !== bPosition) return aPosition - bPosition
    }

    const sequenceDifference = (a.submission_seq ?? Number.MAX_SAFE_INTEGER) -
      (b.submission_seq ?? Number.MAX_SAFE_INTEGER)
    if (sequenceDifference !== 0) return sequenceDifference
    return String(a.job_id).localeCompare(String(b.job_id))
  })

  return (
    <section className="card" aria-labelledby="jobs-table-title">
      <div className="card__header">
        <h2 className="card__title" id="jobs-table-title">Simulation Jobs</h2>
      </div>
      <div className="table-wrapper">
        <table className="table" aria-label="Simulation jobs list">
          <thead>
            <tr>
              <th scope="col">Job ID</th>
              <th scope="col">State</th>
              <th scope="col">Work Total</th>
              <th scope="col">Work Remaining</th>
              <th scope="col">Priority</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={6}>
                  <div className="empty-state">
                    <div className="empty-state__icon">📋</div>
                    No jobs submitted yet. Use the Job Management form to submit a job.
                  </div>
                </td>
              </tr>
            ) : (
              displayJobs.map(job => (
                <tr key={job.job_id}>
                  <td>
                    <code style={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>
                      {job.job_id}
                    </code>
                  </td>
                  <td>
                    <span className={STATE_BADGE[job.state] ?? 'badge'}>
                      {job.state}
                    </span>
                  </td>
                  <td>{job.work_total ?? '—'}</td>
                  <td>{job.work_remaining ?? '—'}</td>
                  <td>{job.priority ?? '—'}</td>
                  <td>
                    {job.state === 'ACTIVE' && (() => {
                      const isEligible = job.can_complete !== undefined
                        ? job.can_complete
                        : (!job.required_cranes || !job.active_cranes || job.active_cranes.length >= job.required_cranes)
                      const isUndersupplied = job.can_complete === false ||
                        (job.required_cranes && job.active_cranes && job.active_cranes.length < job.required_cranes)

                      return (
                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}>
                          <button
                            id={`btn-complete-job-${job.job_id}`}
                            className="btn btn--outline btn--sm"
                            onClick={() => onComplete?.(job.job_id)}
                            disabled={loading[`complete-${job.job_id}`] || !isEligible}
                            title={!isEligible ? 'Cannot complete: missing required crane(s). Recover cranes before completing.' : 'Mark job completed'}
                          >
                            {loading[`complete-${job.job_id}`] ? 'Completing…' : 'Complete'}
                          </button>
                          {isUndersupplied && (
                            <span
                              className="badge"
                              style={{ background: '#fef3c7', color: '#92400e', fontSize: '0.6875rem' }}
                              title="Job is missing one or more required cranes"
                            >
                              undersupplied
                            </span>
                          )}
                        </div>
                      )
                    })()}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
