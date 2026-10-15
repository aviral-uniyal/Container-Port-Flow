import { useState } from 'react'

/**
 * JobForm — job-submission form using the exact API field names:
 *   job_id, arrival_time, work_total, priority, berths, cranes, trucks.
 *
 * Calls props.onSubmit(formData) on submit.
 * API integration is NOT implemented in this milestone.
 */
export default function JobForm({ onSubmit, disabled }) {
  const [form, setForm] = useState({
    job_id:       '',
    arrival_time: 0,
    work_total:   10,
    priority:     1,
    berths:       1,
    cranes:       1,
    trucks:       0,
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
    if (onSubmit) onSubmit({ ...form })
  }

  return (
    <section className="card" aria-labelledby="jobform-title">
      <div className="card__header">
        <h2 className="card__title" id="jobform-title">Job Management</h2>
      </div>
      <div className="card__body">
        <form className="form" id="job-submit-form" onSubmit={handleSubmit}>

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
              disabled={disabled}
            >
              {disabled ? 'Submitting…' : 'Submit Job'}
            </button>
          </div>
        </form>
      </div>
    </section>
  )
}
