/** StatusBar shows only backend, C++ bridge, and database connection health. */
export default function StatusBar({ connectionStatus, healthDetail }) {
  const dotClass = {
    ok:      'status-dot--ok',
    warn:    'status-dot--warn',
    err:     'status-dot--err',
    neutral: 'status-dot--neutral',
  }[connectionStatus] ?? 'status-dot--neutral'

  const connectionLabel = {
    ok:      'Backend connected',
    warn:    'Backend degraded',
    err:     'Backend unreachable',
    neutral: 'Not connected',
  }[connectionStatus] ?? 'Unknown'

  return (
    <div className="status-area" role="status" aria-label="Connection status">
      <div className="status-area__indicator">
        <span className={`status-dot ${dotClass}`} aria-hidden="true" />
        <span>{connectionLabel}</span>
      </div>

      {healthDetail && (
        <div className="status-area__detail">
          <span className={`status-dot ${healthDetail.bridge_alive ? 'status-dot--ok' : 'status-dot--err'}`}
                aria-hidden="true" />
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--clr-text-secondary)' }}>
            Bridge
          </span>
          <span className={`status-dot ${healthDetail.db_connected ? 'status-dot--ok' : 'status-dot--err'}`}
                aria-hidden="true"
                style={{ marginLeft: 'var(--space-2)' }} />
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--clr-text-secondary)' }}>
            DB
          </span>
        </div>
      )}

    </div>
  )
}
