import { useState } from 'react'

const POLICIES = ['FCFS', 'SJF', 'PRIORITY']

/**
 * SimulationSetup — form for initialising the port dispatcher.
 * Fields: berths, cranes, trucks, and scheduling policy.
 * Calls props.onSubmit(formData) when the form is submitted.
 * API integration is NOT implemented in this milestone.
 */
export default function SimulationSetup({ onSubmit, disabled }) {
  const [form, setForm] = useState({
    berths: 4,
    cranes: 2,
    trucks: 6,
    policy: 'FCFS',
  })

  function handleChange(e) {
    const { name, value, type } = e.target
    setForm(prev => ({
      ...prev,
      [name]: type === 'number' ? Number(value) : value,
    }))
  }

  function handleSubmit(e) {
    e.preventDefault()
    if (onSubmit) onSubmit(form)
  }

  return (
    <section className="card" aria-labelledby="setup-title">
      <div className="card__header">
        <h2 className="card__title" id="setup-title">Simulation Setup</h2>
      </div>
      <div className="card__body">
        <form className="form" id="simulation-setup-form" onSubmit={handleSubmit}>
          <div className="form__row">
            <div className="field">
              <label className="field__label" htmlFor="setup-berths">
                Berths (≥ 1)
              </label>
              <input
                id="setup-berths"
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
              <label className="field__label" htmlFor="setup-cranes">
                Cranes (≥ 1)
              </label>
              <input
                id="setup-cranes"
                name="cranes"
                type="number"
                min="1"
                className="field__input"
                value={form.cranes}
                onChange={handleChange}
                required
              />
            </div>
          </div>

          <div className="form__row">
            <div className="field">
              <label className="field__label" htmlFor="setup-trucks">
                Trucks (≥ 0)
              </label>
              <input
                id="setup-trucks"
                name="trucks"
                type="number"
                min="0"
                className="field__input"
                value={form.trucks}
                onChange={handleChange}
                required
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="setup-policy">
                Scheduling Policy
              </label>
              <select
                id="setup-policy"
                name="policy"
                className="field__select"
                value={form.policy}
                onChange={handleChange}
              >
                {POLICIES.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="form__actions">
            <button
              id="btn-initialize-simulation"
              type="submit"
              className="btn btn--primary"
              disabled={disabled}
            >
              {disabled ? 'Initializing…' : 'Initialize Simulation'}
            </button>
          </div>
        </form>
      </div>
    </section>
  )
}
