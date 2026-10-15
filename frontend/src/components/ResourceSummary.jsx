/**
 * ResourceSummary — three cards showing available vs. total for berths,
 * cranes, and trucks.
 *
 * Props:
 *   resources — { berths, cranes, trucks } each with { available, total }
 *               If null/undefined the cards show "— / —" (no data yet).
 */
export default function ResourceSummary({ resources }) {
  const items = [
    { key: 'berths', label: 'Berths' },
    { key: 'cranes', label: 'Cranes' },
    { key: 'trucks', label: 'Trucks' },
  ]

  return (
    <section aria-labelledby="resources-title">
      <h2 className="section-title" id="resources-title">Resource Summary</h2>
      <div className="resource-grid">
        {items.map(({ key, label }) => {
          const r = resources?.[key]
          const available = r?.available ?? null
          const total     = r?.total     ?? null
          const display   = total !== null ? `${available}` : '—'
          const sub       = total !== null ? `of ${total} total` : 'No data yet'

          return (
            <div className="resource-card" key={key} aria-label={`${label} resource summary`}>
              <div className="resource-card__label">{label}</div>
              <div className="resource-card__value">{display}</div>
              <div className="resource-card__sub">{sub}</div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
