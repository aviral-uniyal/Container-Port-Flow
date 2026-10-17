import { useState } from 'react'

/**
 * CraneRegistry — shows registered cranes with their status, assigned job,
 * and action buttons for failure/recovery.
 *
 * Props:
 *   cranes         — array of crane objects: { crane_id, status, assigned_job_id? }
 *   onRegister     — callback(crane_id) to register a new crane
 *   onFail         — callback(crane_id) to mark a crane as failed
 *   onRecover      — callback(crane_id) to recover a failed crane
 *   disabled       — boolean, disables the register button while a request is running
 *   loading        — object tracking in-flight requests per action key
 *   lastFailResult — { crane_id, affected_job_id, job_interrupted, ... } | null
 */
export default function CraneRegistry({
  cranes = [],
  inventory,
  onRegister,
  onFail,
  onRecover,
  onReconcile,
  disabled,
  loading = {},
  lastFailResult,
}) {
  const [craneId, setCraneId] = useState('')
  const registeredIds = new Set(cranes.map(crane => crane.crane_id))
  const eligible = (inventory?.cranes ?? []).filter(
    crane => crane.status === 'AVAILABLE' && !registeredIds.has(crane.crane_id)
  )
  const selectedCraneId = eligible.some(crane => crane.crane_id === craneId) ? craneId : ''

  const STATUS_BADGE = {
    AVAILABLE: 'badge badge--available',
    ASSIGNED:  'badge badge--assigned',
    FAILED:    'badge badge--failed',
  }

  function handleSubmit(e) {
    e.preventDefault()
    if (selectedCraneId && onRegister) {
      onRegister(selectedCraneId)
      setCraneId('')
    }
  }

  return (
    <section className="card" aria-labelledby="cranes-table-title">
      <div className="card__header">
        <h2 className="card__title" id="cranes-table-title">Crane Registry</h2>
      </div>

      {/* ── Registration form ── */}
      {onRegister && (
        <div className="card__body" style={{ borderBottom: '1px solid var(--clr-border)', paddingBottom: 'var(--space-4)' }}>
          <form className="form" id="crane-register-form" onSubmit={handleSubmit}
                style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 'var(--space-3)' }}>
            <div className="field" style={{ flex: 1 }}>
              <label className="field__label" htmlFor="crane-id-input">Crane ID</label>
              <select
                id="crane-id-input"
                className="field__input"
                value={selectedCraneId}
                onChange={e => setCraneId(e.target.value)}
                required
                disabled={eligible.length === 0}
              >
                <option value="">Select an available crane</option>
                {eligible.map(crane => (
                  <option key={crane.crane_id} value={crane.crane_id}>{crane.crane_id}</option>
                ))}
              </select>
            </div>
            <button
              id="btn-register-crane"
              type="submit"
              className="btn btn--primary btn--sm"
              disabled={disabled || !selectedCraneId}
            >
              {disabled ? 'Registering…' : 'Register Crane'}
            </button>
          </form>
          {eligible.length === 0 && (
            <p style={{ color: 'var(--clr-text-muted)', fontSize: 'var(--text-sm)' }}>
              No eligible database cranes are available to register.
            </p>
          )}
        </div>
      )}

      {/* ── Disruption result banner ── */}
      {lastFailResult && lastFailResult.job_interrupted && (
        <div
          className="card__body"
          style={{
            borderBottom: '1px solid var(--clr-border)',
            padding: 'var(--space-3) var(--space-5)',
            background: 'var(--clr-badge-failed)',
            fontSize: 'var(--text-sm)',
            color: 'var(--clr-badge-failed-t)',
          }}
        >
          ⚠️ Crane <strong>{lastFailResult.crane_id}</strong> failure interrupted
          job <strong>{lastFailResult.affected_job_id}</strong>.
          {lastFailResult.work_remaining != null && (
            <> Work remaining: {lastFailResult.work_remaining} units.</>
          )}
          {lastFailResult.db_sync_warning && (
            <div style={{ marginTop: 'var(--space-1)', color: 'var(--clr-err)' }}>
              ⚠ {lastFailResult.db_sync_warning}
              {onReconcile && (
                <button
                  id="btn-crane-reconcile-sync"
                  className="btn btn--outline btn--sm"
                  style={{ marginLeft: 'var(--space-3)' }}
                  onClick={onReconcile}
                  disabled={loading.reconcile}
                >
                  {loading.reconcile ? 'Syncing…' : '⚖ Reconcile Now'}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Crane table ── */}
      <div className="table-wrapper">
        <table className="table" aria-label="Crane registry list">
          <thead>
            <tr>
              <th scope="col">Crane ID</th>
              <th scope="col">Status</th>
              <th scope="col">Assigned Job</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {cranes.length === 0 ? (
              <tr>
                <td colSpan={4}>
                  <div className="empty-state">
                    <div className="empty-state__icon">🏗️</div>
                    No cranes registered yet.
                  </div>
                </td>
              </tr>
            ) : (
              cranes.map(crane => {
                const failKey = `fail-${crane.crane_id}`
                const recoverKey = `recover-${crane.crane_id}`
                const isFailing = loading[failKey]
                const isRecovering = loading[recoverKey]
                const isBusy = isFailing || isRecovering

                return (
                  <tr key={crane.crane_id}>
                    <td>
                      <code style={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>
                        {crane.crane_id}
                      </code>
                    </td>
                    <td>
                      <span className={STATUS_BADGE[crane.status] ?? 'badge'}>
                        {crane.status}
                      </span>
                    </td>
                    <td style={{ color: crane.assigned_job_id ? 'inherit' : 'var(--clr-text-muted)' }}>
                      {crane.assigned_job_id ?? 'None'}
                    </td>
                    <td>
                      {crane.status === 'AVAILABLE' && onFail && (
                        <button
                          id={`btn-fail-${crane.crane_id}`}
                          className="btn btn--danger btn--sm"
                          onClick={() => onFail(crane.crane_id)}
                          disabled={isBusy}
                        >
                          {isFailing ? 'Failing…' : '⚠ Mark as Failed'}
                        </button>
                      )}
                      {crane.status === 'ASSIGNED' && onFail && (
                        <button
                          id={`btn-fail-${crane.crane_id}`}
                          className="btn btn--danger btn--sm"
                          onClick={() => {
                            if (window.confirm(
                              `Crane "${crane.crane_id}" is assigned to job "${crane.assigned_job_id}". ` +
                              'Failing it may interrupt the active job. Continue?'
                            )) {
                              onFail(crane.crane_id)
                            }
                          }}
                          disabled={isBusy}
                          title={`Warning: assigned to job ${crane.assigned_job_id}`}
                        >
                          {isFailing ? 'Failing…' : '⚠ Mark as Failed'}
                        </button>
                      )}
                      {crane.status === 'FAILED' && onRecover && (
                        <button
                          id={`btn-recover-${crane.crane_id}`}
                          className="btn btn--success btn--sm"
                          onClick={() => onRecover(crane.crane_id)}
                          disabled={isBusy}
                        >
                          {isRecovering ? 'Recovering…' : '✓ Recover Crane'}
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
