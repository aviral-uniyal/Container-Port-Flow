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
              jobs.map(job => (
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
                    {job.state === 'ACTIVE' && (
                      <button
                        id={`btn-complete-job-${job.job_id}`}
                        className="btn btn--outline btn--sm"
                        onClick={() => onComplete?.(job.job_id)}
                        disabled={loading[`complete-${job.job_id}`]}
                      >
                        {loading[`complete-${job.job_id}`] ? 'Completing…' : 'Complete'}
                      </button>
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
