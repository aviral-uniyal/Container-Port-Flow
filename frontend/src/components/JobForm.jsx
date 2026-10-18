import { useEffect, useRef, useState } from 'react'

export function selectableShips(ships) {
  return Array.isArray(ships) ? ships.filter(ship => ship.status === 'NOT ARRIVED') : []
}

export function matchingShips(ships, query) {
  const needle = query.trim().toLocaleLowerCase()
  return selectableShips(ships).filter(ship =>
    ship.vessel_name.toLocaleLowerCase().includes(needle)
  )
}

export function nextShipIndex(current, count, key) {
  if (count === 0) return -1
  if (key === 'ArrowDown') return (current + 1) % count
  if (key === 'ArrowUp') return current <= 0 ? count - 1 : current - 1
  return current
}

/**
 * JobForm — job-submission form using the exact API field names:
 *   ship_id, job_id, arrival_time, work_total, priority, berths, cranes, trucks.
 *
 * Calls props.onSubmit(formData) on submit.
 */
export default function JobForm({ onSubmit, disabled, ships, inventoryLoading, inventoryError }) {
  const shipSearchRef = useRef(null)
  const shipOptionsRef = useRef(null)
  const [shipQuery, setShipQuery] = useState('')
  const [shipOpen, setShipOpen] = useState(false)
  const [activeShipIndex, setActiveShipIndex] = useState(-1)
  const [form, setForm] = useState({
    ship_id:      null,
    job_id:       '',
    arrival_time: 0,
    work_total:   10,
    priority:     1,
    berths:       1,
    cranes:       1,
    trucks:       0,
  })

  const choices = matchingShips(ships, shipQuery)
  const selectedShip = Array.isArray(ships)
    ? ships.find(ship => ship.ship_id === form.ship_id)
    : null
  const validShip = selectedShip?.status === 'NOT ARRIVED'
  const shipListAvailable = Array.isArray(ships) && !inventoryLoading && !inventoryError

  useEffect(() => {
    if (shipOpen && activeShipIndex >= 0) {
      shipOptionsRef.current?.children[activeShipIndex]?.scrollIntoView({ block: 'nearest' })
    }
  }, [shipOpen, activeShipIndex, shipQuery])

  function selectShip(ship) {
    setForm(prev => ({ ...prev, ship_id: ship.ship_id }))
    setShipQuery(ship.vessel_name)
    setShipOpen(false)
    setActiveShipIndex(-1)
  }

  function clearShip() {
    setForm(prev => ({ ...prev, ship_id: null }))
    setShipQuery('')
    setActiveShipIndex(0)
    setShipOpen(true)
    shipSearchRef.current?.focus()
  }

  function handleShipKeyDown(e) {
    if (!shipListAvailable) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setShipOpen(true)
      setActiveShipIndex(nextShipIndex(activeShipIndex, choices.length, e.key))
    } else if (e.key === 'Enter' && shipOpen && activeShipIndex >= 0) {
      e.preventDefault()
      if (choices[activeShipIndex]) selectShip(choices[activeShipIndex])
    } else if (e.key === 'Escape') {
      setShipOpen(false)
      setActiveShipIndex(-1)
    }
  }

  function handleChange(e) {
    const { name, value, type } = e.target
    setForm(prev => ({
      ...prev,
      [name]: type === 'number' ? Number(value) : value,
    }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!validShip || !shipListAvailable || !onSubmit) return
    const submitted = await onSubmit({ ...form })
    if (submitted) clearShip()
  }

  return (
    <section className="card" aria-labelledby="jobform-title">
      <div className="card__header">
        <h2 className="card__title" id="jobform-title">Job Management</h2>
      </div>
      <div className="card__body">
        <form className="form" id="job-submit-form" onSubmit={handleSubmit}>

          <div className="field ship-selector">
            <label className="field__label" htmlFor="job-ship-search">Ship</label>
            <div className="ship-selector__control">
              <input
                ref={shipSearchRef}
                id="job-ship-search"
                className="field__input"
                type="text"
                role="combobox"
                aria-autocomplete="list"
                aria-controls="job-ship-options"
                aria-expanded={shipOpen && shipListAvailable}
                aria-activedescendant={shipOpen && choices[activeShipIndex] ? `job-ship-option-${choices[activeShipIndex].ship_id}` : undefined}
                aria-required="true"
                autoComplete="off"
                placeholder="Search and select a ship"
                value={shipQuery}
                onChange={e => {
                  setShipQuery(e.target.value)
                  setForm(prev => ({ ...prev, ship_id: null }))
                  setActiveShipIndex(0)
                  setShipOpen(true)
                }}
                onFocus={() => setShipOpen(true)}
                onBlur={() => setShipOpen(false)}
                onKeyDown={handleShipKeyDown}
                disabled={!shipListAvailable || disabled}
              />
              {shipQuery && (
                <button type="button" className="ship-selector__clear" onClick={clearShip}
                  aria-label="Clear selected ship" disabled={disabled}>
                  ×
                </button>
              )}
              {shipOpen && shipListAvailable && (
              <div ref={shipOptionsRef} id="job-ship-options" className="ship-selector__options" role="listbox" aria-label="Available ships">
                {choices.length === 0 ? (
                  <div className="ship-selector__empty">No available ships match your search.</div>
                ) : choices.map((ship, index) => (
                  <div key={ship.ship_id} id={`job-ship-option-${ship.ship_id}`}
                    className={`ship-selector__option${index === activeShipIndex ? ' ship-selector__option--active' : ''}`}
                    role="option" aria-selected={ship.ship_id === form.ship_id}
                    onMouseDown={e => e.preventDefault()}
                    onClick={() => selectShip(ship)}>
                    {ship.vessel_name}
                  </div>
                ))}
              </div>
              )}
            </div>
            {inventoryError ? (
              <span className="field__hint field__hint--error">Ship inventory unavailable: {inventoryError}</span>
            ) : inventoryLoading ? (
              <span className="field__hint">Loading ships from PostgreSQL…</span>
            ) : !Array.isArray(ships) ? (
              <span className="field__hint">Ship inventory is not loaded yet.</span>
            ) : !validShip && form.ship_id !== null ? (
              <span className="field__hint field__hint--error">This ship already has a job in the current run. Choose another ship.</span>
            ) : selectableShips(ships).length === 0 ? (
              <span className="field__hint">No ships are available for this run.</span>
            ) : (
              <span className="field__hint">Choose one available ship. Each ship can have one job per run.</span>
            )}
          </div>

          {/* Row 1: job_id + arrival_time */}
          <div className="form__row">
            <div className="field">
              <label className="field__label" htmlFor="job-job-id">
                job_id
              </label>
              <input
                id="job-job-id"
                name="job_id"
                type="text"
                className="field__input"
                value={form.job_id}
                onChange={handleChange}
                placeholder="e.g. VESSEL-001"
                required
                minLength={1}
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="job-arrival-time">
                arrival_time (≥ 0)
              </label>
              <input
                id="job-arrival-time"
                name="arrival_time"
                type="number"
                min="0"
                className="field__input"
                value={form.arrival_time}
                onChange={handleChange}
                required
              />
            </div>
          </div>

          {/* Row 2: work_total + priority */}
          <div className="form__row">
            <div className="field">
              <label className="field__label" htmlFor="job-work-total">
                work_total (≥ 1)
              </label>
              <input
                id="job-work-total"
                name="work_total"
                type="number"
                min="1"
                className="field__input"
                value={form.work_total}
                onChange={handleChange}
                required
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="job-priority">
                priority (≥ 0)
              </label>
              <input
                id="job-priority"
                name="priority"
                type="number"
                min="0"
                className="field__input"
                value={form.priority}
                onChange={handleChange}
                required
              />
            </div>
          </div>

          {/* Row 3: berths + cranes + trucks */}
          <div className="form__row form__row--triple">
            <div className="field">
              <label className="field__label" htmlFor="job-berths">
                berths (≥ 1)
              </label>
              <input
                id="job-berths"
                name="berths"
                type="number"
                min="1"
                className="field__input"
                value={form.berths}
                onChange={handleChange}
                required
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="job-cranes">
                cranes (≥ 0)
              </label>
              <input
                id="job-cranes"
                name="cranes"
                type="number"
                min="0"
                className="field__input"
                value={form.cranes}
                onChange={handleChange}
                required
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="job-trucks">
                trucks (≥ 0)
              </label>
              <input
                id="job-trucks"
                name="trucks"
                type="number"
                min="0"
                className="field__input"
                value={form.trucks}
                onChange={handleChange}
                required
              />
            </div>
          </div>

          <div className="form__actions">
            <button
              id="btn-submit-job"
              type="submit"
              className="btn btn--primary"
              disabled={disabled || !validShip || !shipListAvailable}
            >
              {disabled ? 'Submitting…' : 'Submit Job'}
            </button>
          </div>
        </form>
      </div>
    </section>
  )
}
