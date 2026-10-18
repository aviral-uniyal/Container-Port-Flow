import { useState } from 'react'

export function filterInventoryShips(ships, query) {
  const needle = query.trim().toLocaleLowerCase()
  return ships.filter(ship => ship.vessel_name.toLocaleLowerCase().includes(needle))
}

/**
 * DatabaseInventory — displays physical infrastructure records from PostgreSQL.
 *
 * Props:
 *   inventory — { terminals, berths, cranes, ships } from GET /inventory, or null.
 *   loading   — boolean, true while the request is in-flight.
 *   error     — string | null, populated when the fetch failed.
 *
 * Clearly labelled as "Database Inventory (PostgreSQL)" to distinguish it from
 * the volatile C++ simulation runtime snapshot shown in ResourceSummary.
 */
export default function DatabaseInventory({ inventory, loading, error }) {
  const [shipSearch, setShipSearch] = useState('')
  const STATUS_BADGE = {
    AVAILABLE: 'badge badge--available',
    ASSIGNED:  'badge badge--assigned',
    FAILED:    'badge badge--failed',
  }

  /* ── Loading state ── */
  if (loading) {
    return (
      <section className="card" aria-labelledby="db-inventory-title">
        <div className="card__header">
          <h2 className="card__title" id="db-inventory-title">
            Database Inventory <span style={{ fontWeight: 400, fontSize: '0.75rem', color: 'var(--clr-text-muted)' }}>(PostgreSQL)</span>
          </h2>
        </div>
        <div className="card__body">
          <p style={{ color: 'var(--clr-text-muted)', fontSize: 'var(--text-sm)' }}>Loading inventory…</p>
        </div>
      </section>
    )
  }

  /* ── Error state ── */
  if (error) {
    return (
      <section className="card" aria-labelledby="db-inventory-title">
        <div className="card__header">
          <h2 className="card__title" id="db-inventory-title">
            Database Inventory <span style={{ fontWeight: 400, fontSize: '0.75rem', color: 'var(--clr-text-muted)' }}>(PostgreSQL)</span>
          </h2>
        </div>
        <div className="card__body">
          <p style={{ color: 'var(--clr-accent-err, #c0392b)', fontSize: 'var(--text-sm)' }}>
            Could not load inventory: {error}
          </p>
        </div>
      </section>
    )
  }

  /* ── Empty / not-loaded state ── */
  if (!inventory) {
    return (
      <section className="card" aria-labelledby="db-inventory-title">
        <div className="card__header">
          <h2 className="card__title" id="db-inventory-title">
            Database Inventory <span style={{ fontWeight: 400, fontSize: '0.75rem', color: 'var(--clr-text-muted)' }}>(PostgreSQL)</span>
          </h2>
        </div>
        <div className="card__body">
          <p style={{ color: 'var(--clr-text-muted)', fontSize: 'var(--text-sm)' }}>
            No inventory loaded. Connect the database to view records.
          </p>
        </div>
      </section>
    )
  }

  const { terminals = [], berths = [], cranes = [], ships = [] } = inventory
  const filteredShips = filterInventoryShips(ships, shipSearch)

  /* Build a lookup: terminal_id → terminal_name */
  const terminalName = Object.fromEntries(
    terminals.map(t => [t.terminal_id, t.terminal_name])
  )

  return (
    <section className="card" aria-labelledby="db-inventory-title">
      <div className="card__header">
        <h2 className="card__title" id="db-inventory-title">
          Database Inventory{' '}
          <span style={{ fontWeight: 400, fontSize: '0.75rem', color: 'var(--clr-text-muted)' }}>
            (PostgreSQL)
          </span>
        </h2>
        {/* Summary counts */}
        <div style={{ display: 'flex', gap: 'var(--space-4)', fontSize: 'var(--text-xs)', color: 'var(--clr-text-secondary)' }}>
          <span>{terminals.length} terminal{terminals.length !== 1 ? 's' : ''}</span>
          <span>{berths.length} berth{berths.length !== 1 ? 's' : ''}</span>
          <span>{cranes.length} crane{cranes.length !== 1 ? 's' : ''}</span>
          <span>{ships.length} ship{ships.length !== 1 ? 's' : ''}</span>
        </div>
      </div>

      {/* ── No data at all ── */}
      {terminals.length === 0 && berths.length === 0 && cranes.length === 0 && (
        <div className="card__body">
          <div className="empty-state">
            <div className="empty-state__icon">🏗️</div>
            No infrastructure records found. Run the demo seed to populate terminals, berths, and cranes.
          </div>
        </div>
      )}

      {/* ── Terminals ── */}
      {terminals.length > 0 && (
        <div style={{ borderBottom: '1px solid var(--clr-border)' }}>
          <div style={{ padding: 'var(--space-2) var(--space-5)', fontWeight: 600, fontSize: 'var(--text-xs)', color: 'var(--clr-text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Terminals
          </div>
          <div className="table-wrapper">
            <table className="table" aria-label="Terminal records">
              <thead>
                <tr>
                  <th scope="col">ID</th>
                  <th scope="col">Name</th>
                  <th scope="col">Description</th>
                </tr>
              </thead>
              <tbody>
                {terminals.map(t => (
                  <tr key={t.terminal_id}>
                    <td style={{ color: 'var(--clr-text-muted)', fontFamily: 'monospace', fontSize: '0.8125rem' }}>{t.terminal_id}</td>
                    <td>{t.terminal_name}</td>
                    <td style={{ color: 'var(--clr-text-secondary)', fontSize: 'var(--text-sm)' }}>{t.description ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Berths ── */}
      {berths.length > 0 && (
        <div style={{ borderBottom: '1px solid var(--clr-border)' }}>
          <div style={{ padding: 'var(--space-2) var(--space-5)', fontWeight: 600, fontSize: 'var(--text-xs)', color: 'var(--clr-text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Berths
          </div>
          <div className="table-wrapper">
            <table className="table" aria-label="Berth records">
              <thead>
                <tr>
                  <th scope="col">ID</th>
                  <th scope="col">Name</th>
                  <th scope="col">Terminal</th>
                </tr>
              </thead>
              <tbody>
                {berths.map(b => (
                  <tr key={b.berth_id}>
                    <td style={{ color: 'var(--clr-text-muted)', fontFamily: 'monospace', fontSize: '0.8125rem' }}>{b.berth_id}</td>
                    <td><code style={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>{b.berth_name}</code></td>
                    <td style={{ color: 'var(--clr-text-secondary)', fontSize: 'var(--text-sm)' }}>{terminalName[b.terminal_id] ?? `#${b.terminal_id}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Cranes ── */}
      {cranes.length > 0 && (
        <div>
          <div style={{ padding: 'var(--space-2) var(--space-5)', fontWeight: 600, fontSize: 'var(--text-xs)', color: 'var(--clr-text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Cranes
          </div>
          <div className="table-wrapper">
            <table className="table" aria-label="Crane records from database">
              <thead>
                <tr>
                  <th scope="col">Crane ID</th>
                  <th scope="col">Status</th>
                  <th scope="col">Terminal</th>
                  <th scope="col">Last Updated</th>
                </tr>
              </thead>
              <tbody>
                {cranes.map(c => (
                  <tr key={c.crane_id}>
                    <td><code style={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>{c.crane_id}</code></td>
                    <td>
                      <span className={STATUS_BADGE[c.status] ?? 'badge'}>
                        {c.status}
                      </span>
                    </td>
                    <td style={{ color: 'var(--clr-text-secondary)', fontSize: 'var(--text-sm)' }}>
                      {terminalName[c.terminal_id] ?? `#${c.terminal_id}`}
                    </td>
                    <td style={{ color: 'var(--clr-text-muted)', fontSize: 'var(--text-sm)' }}>
                      {c.updated_at
                        ? new Date(c.updated_at).toLocaleString()
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="ships-inventory" aria-labelledby="db-ships-title">
        <div className="ships-inventory__header">
          <h3 id="db-ships-title">Ships <span>({ships.length} total)</span></h3>
          <label className="ships-inventory__search-label" htmlFor="db-ships-search">Search ships</label>
          <input id="db-ships-search" type="search" className="field__input ships-inventory__search"
            value={shipSearch} onChange={e => setShipSearch(e.target.value)}
            placeholder="Search by ship name" />
        </div>
        <div className="ships-inventory__list" role="region" aria-label="Ships from PostgreSQL" tabIndex={0}>
          {filteredShips.length === 0 ? (
            <p className="ships-inventory__empty">
              {ships.length === 0 ? 'No ship records found in PostgreSQL.' : 'No ships match your search.'}
            </p>
          ) : filteredShips.map(ship => (
            <div className="ships-inventory__row" key={ship.ship_id}>
              <span>{ship.vessel_name}</span>
              <span className={`ship-status ship-status--${ship.status === 'IN OPERATION' ? 'active' : ship.status === 'COMPLETED & LEFT' ? 'complete' : 'waiting'}`}>
                {ship.status}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
