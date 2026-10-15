/**
 * StatusBar — shows backend connection status and the most recent operation
 * feedback message. Accepts `connectionStatus` ('ok' | 'warn' | 'err' | 'neutral'),
 * an optional `healthDetail` object with { bridge_alive, db_connected },
 * and an optional `message` string with a severity ('ok' | 'warn' | 'err' | null).
 */
export default function StatusBar({ connectionStatus, healthDetail, message, messageSeverity }) {
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

  const msgClass = messageSeverity
    ? `status-area__message status-area__message--${messageSeverity}`
    : 'status-area__message'

  return (
    <div className="status-area" role="status" aria-live="polite">
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

      {message && (
        <p className={msgClass}>{message}</p>
      )}

      {!message && (
        <p className="status-area__message">
          No operations performed yet.
        </p>
      )}
    </div>
  )
}
