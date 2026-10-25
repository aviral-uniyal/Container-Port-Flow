/**
 * JobsTable — shows submitted simulation jobs.
 * Columns: job ID, state, crane supply, queue position, work total, work completed,
 * work remaining, priority, actions.
 *
 * Props:
 *   jobs       — array of job objects. Empty array shows an empty-state row.
 *   onComplete — callback(job_id) for the Complete action button.
 *   onDispatch — callback() for the Dispatch All button (batch mode only).
 *   batchMode  — boolean; when true shows the Dispatch All banner.
 *   loading    — object tracking in-flight request keys.
 */
export function jobSupplyLabel(job) {
  if (job.state !== 'ACTIVE' && job.state !== 'INTERRUPTED') return '—'

  const fulfilled = job.crane_fulfilled !== undefined
    ? job.crane_fulfilled
    : job.can_complete !== undefined
      ? job.can_complete
      : (!job.required_cranes || !job.active_cranes || job.active_cranes.length >= job.required_cranes)
  return fulfilled ? 'FULLY SUPPLIED' : 'UNDER-SUPPLIED'
}

export default function JobsTable({ jobs = [], onComplete, onDispatch, batchMode = false, loading = {} }) {
  const STATE_BADGE = {
    PENDING:     'badge badge--pending',
    ACTIVE:      'badge badge--active',
    COMPLETED:   'badge badge--completed',
    INTERRUPTED: 'badge badge--failed',
    CANCELLED:   'badge badge--failed',
    FAILED:      'badge badge--failed',
  }

  return (
    <section className="card" aria-labelledby="jobs-table-title">
      <div className="card__header">
        <h2 className="card__title" id="jobs-table-title">Simulation Jobs</h2>

        {/* Dispatch All banner — only visible in batch mode */}
        {batchMode && (
          <div className="batch-banner" role="status">
            <span className="batch-banner__text">
              ⏸ Batch mode
              {jobs.some(j => j.state === 'PENDING') && (
                <span className="batch-banner__count">
                  {jobs.filter(j => j.state === 'PENDING').length} pending
                </span>
              )}
            </span>
            <button
              id="btn-dispatch-all"
              type="button"
              className="btn btn--primary btn--sm"
              onClick={onDispatch}
              disabled={loading.dispatch}
              title="Dispatch all queued jobs using the active scheduling policy"
            >
              {loading.dispatch ? 'Dispatching…' : '▶▶ Dispatch All'}
            </button>
          </div>
        )}
      </div>
      <p className="jobs-table__hint">
        Jobs are placed by the selected scheduling policy when submitted. Rows keep their positions
        as job states change. State and crane supply are shown separately.
      </p>
      <div className="table-wrapper">
        <table className="table" aria-label="Simulation jobs list">
          <thead>
            <tr>
              <th scope="col">Job ID</th>
              <th scope="col">Arrival Time</th>
              <th scope="col">State</th>
              <th scope="col">Crane Supply</th>
              <th scope="col">Queue Position</th>
              <th scope="col">Work Total</th>
              <th scope="col">Work Completed</th>
              <th scope="col">Work Remaining</th>
              <th scope="col">Priority</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={10}>
                  <div className="empty-state">
                    <div className="empty-state__icon">📋</div>
                    No jobs submitted yet. Use the Job Management form to submit a job.
                  </div>
                </td>
              </tr>
            ) : (
              jobs.map(job => (
                <tr key={job.job_id}>
                  <td>
                    <code style={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>
                      {job.job_id}
                    </code>
                  </td>
                  <td>{job.arrival_time ?? '—'}</td>
                  <td>
                    <span className={STATE_BADGE[job.state] ?? 'badge'}>
                      {job.state}
                    </span>
                  </td>
                  <td>
                    {jobSupplyLabel(job) === 'UNDER-SUPPLIED' ? (
                      <span
                        className="badge"
                        style={{ background: '#fef3c7', color: '#92400e', fontSize: '0.6875rem' }}
                        title="Job is missing one or more required cranes"
                      >
                        UNDER-SUPPLIED
                      </span>
                    ) : jobSupplyLabel(job)}
                  </td>
                  <td>
                    {(job.state === 'PENDING' || job.state === 'INTERRUPTED') &&
                      Number.isInteger(job.queue_position) && job.queue_position > 0
                      ? job.queue_position
                      : '—'}
                  </td>
                  <td>{job.work_total ?? '—'}</td>
                  <td>{job.work_completed ?? '—'}</td>
                  <td>{job.work_remaining ?? '—'}</td>
                  <td>{job.priority ?? '—'}</td>
                  <td>
                    {job.state === 'ACTIVE' && (() => {
                      const isEligible = job.can_complete !== undefined
                        ? job.can_complete
                        : (!job.required_cranes || !job.active_cranes || job.active_cranes.length >= job.required_cranes)
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
                        </div>
                      )
                    })()}
                    {job.state === 'PENDING' && batchMode && (
                      <span
                        className="badge batch-pending-hint"
                        title="Queued in batch mode — click Dispatch All to send"
                      >
                        awaiting dispatch
                      </span>
                    )}
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
