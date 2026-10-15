import { useState } from 'react'

/**
 * CraneRegistry — shows registered cranes with their status and assigned job.
 * Includes an inline registration form when `onRegister` is provided.
 *
 * Props:
 *   cranes     — array of crane objects: { crane_id, status, assigned_job_id? }
 *   onRegister — callback(crane_id) to register a new crane
 *   disabled   — boolean, disables the register button while a request is running
 */
export default function CraneRegistry({ cranes = [], onRegister, disabled }) {
  const [craneId, setCraneId] = useState('')

  const STATUS_BADGE = {
    AVAILABLE: 'badge badge--available',
    ASSIGNED:  'badge badge--assigned',
    FAILED:    'badge badge--failed',
  }

  function handleSubmit(e) {
    e.preventDefault()
    const trimmed = craneId.trim()
    if (trimmed && onRegister) {
      onRegister(trimmed)
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
              <input
                id="crane-id-input"
                type="text"
                className="field__input"
                value={craneId}
                onChange={e => setCraneId(e.target.value)}
                placeholder="e.g. CRANE-01"
                required
                minLength={1}
              />
            </div>
            <button
              id="btn-register-crane"
              type="submit"
              className="btn btn--primary btn--sm"
              disabled={disabled || !craneId.trim()}
            >
              {disabled ? 'Registering…' : 'Register Crane'}
            </button>
          </form>
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
            </tr>
          </thead>
          <tbody>
            {cranes.length === 0 ? (
              <tr>
                <td colSpan={3}>
                  <div className="empty-state">
                    <div className="empty-state__icon">🏗️</div>
                    No cranes registered yet.
                  </div>
                </td>
              </tr>
            ) : (
              cranes.map(crane => (
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
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
