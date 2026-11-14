import { useRef, useState } from 'react'
import { requestBerthDuration } from '../api'

const unavailableMessages = {
  unseen_vessel: 'This vessel was not present in the model training records. A duration estimate is unavailable.',
  missing_vessel: 'Enter a vessel name to request a prediction.',
  invalid_vessel: 'Enter a valid vessel name to request a prediction.',
  missing_model_artifact: 'The trained model is not installed on the server.',
  invalid_model_metadata: 'The model metadata is invalid. An estimate cannot be shown.',
  incompatible_feature_schema: 'The installed model does not match the required input schema.',
  incompatible_runtime: 'The installed model is incompatible with the server runtime.',
  model_artifact_mismatch: 'The installed model and metadata do not match.',
  invalid_artifact_location: 'The model artifact location is invalid.',
  model_load_failed: 'The server could not load the trained model.',
  prediction_failed: 'The model could not produce an estimate for this request.',
  ml_dependencies_unavailable: 'Prediction dependencies are unavailable on the server.',
}

export function predictionOutcome(response) {
  if (!response?.ok) return { state: 'error', message: String(response?.error || 'Prediction request failed.') }
  const data = response.data
  if (data?.status === 'unavailable') {
    return { state: 'unavailable', message: unavailableMessages[data.reason] || data.explanation || 'No estimate is available for this vessel.' }
  }
  if (data?.status !== 'available' || data.designation !== 'experimental_advisory' ||
      data.units !== 'hours' || typeof data.estimated_duration_hours !== 'number' ||
      !Number.isFinite(data.estimated_duration_hours) || data.estimated_duration_hours < 0) {
    return { state: 'error', message: 'The prediction service returned an invalid result.' }
  }
  return { state: 'available', data }
}

export function PredictionResult({ outcome }) {
  if (outcome.state === 'initial') {
    return <p className="prediction-message" role="status">No prediction requested.</p>
  }
  if (outcome.state === 'loading') {
    return <p className="prediction-message" role="status">Requesting prediction…</p>
  }
  if (outcome.state === 'unavailable') {
    return <p className="prediction-message prediction-message--unavailable" role="status">Prediction unavailable. {outcome.message}</p>
  }
  if (outcome.state === 'error') {
    return <p className="prediction-message prediction-message--error" role="alert">Prediction request error. {outcome.message}</p>
  }

  const { data } = outcome
  return (
    <section className="prediction-result" aria-label="Duration prediction" role="status">
      <span className="badge badge--active">Experimental advisory</span>
      <h3 className="prediction-result__vessel">{data.vessel_name}</h3>
      <p className="prediction-result__estimate">{data.estimated_duration_hours.toFixed(2)} <span>hours</span></p>
      <p className="prediction-result__caption">Estimated berth-to-departure duration</p>
      <dl className="prediction-result__details">
        <div><dt>Model version</dt><dd>{data.model_version || 'Not provided'}</dd></div>
        <div><dt>Historical training period</dt><dd>{data.training_period || 'Not provided'}</dd></div>
        <div><dt>Prediction generated</dt><dd>{data.predicted_at || 'Not provided'}</dd></div>
        <div><dt>Prediction moment</dt><dd>{data.prediction_moment || 'actual berthing'}</dd></div>
      </dl>
      <p className="prediction-result__explanation">{data.explanation}</p>
    </section>
  )
}

export default function Predictions({ ships }) {
  const [vesselName, setVesselName] = useState('')
  const [outcome, setOutcome] = useState({ state: 'initial' })
  const requestId = useRef(0)
  const suggestions = [...new Set((Array.isArray(ships) ? ships : [])
    .map(ship => ship.vessel_name).filter(name => typeof name === 'string' && name.trim()))]

  async function handleSubmit(event) {
    event.preventDefault()
    const name = vesselName.trim()
    if (!name) {
      setOutcome({ state: 'unavailable', message: unavailableMessages.missing_vessel })
      return
    }
    const currentRequest = ++requestId.current
    setOutcome({ state: 'loading' })
    const response = await requestBerthDuration(name)
    if (currentRequest === requestId.current) setOutcome(predictionOutcome(response))
  }

  function handleNameChange(event) {
    requestId.current += 1
    setVesselName(event.target.value)
    setOutcome({ state: 'initial' })
  }

  return (
    <>
      <section className="card" aria-labelledby="predictions-title">
        <div className="card__header"><h2 className="card__title" id="predictions-title">Berth Duration Prediction</h2></div>
        <div className="card__body prediction-panel">
          <form className="prediction-form" onSubmit={handleSubmit}>
            <div className="field">
              <label className="field__label" htmlFor="prediction-vessel">Vessel name</label>
              <input id="prediction-vessel" className="field__input" type="text" list="prediction-vessels"
                value={vesselName} onChange={handleNameChange} placeholder="Enter a vessel name" maxLength={200} />
              <datalist id="prediction-vessels">{suggestions.map(name => <option key={name} value={name} />)}</datalist>
              <span className="field__hint">Inventory names are suggestions; some may be unsupported by the historical model.</span>
            </div>
            <button className="btn btn--primary" type="submit" disabled={outcome.state === 'loading'}>
              Generate Prediction
            </button>
          </form>
          <PredictionResult outcome={outcome} />
        </div>
      </section>
      <section className="card" aria-labelledby="prediction-limits-title">
        <div className="card__header"><h2 className="card__title" id="prediction-limits-title">About this estimate</h2></div>
        <div className="card__body prediction-limits">
          <p>Predictions are experimental. The model was developed from historical vessel records from 2019–2021, with 2021 held out for final testing. It estimates berth-to-departure duration at actual berthing.</p>
          <p>Actual operational duration may differ. The model has not been validated for current port operations. This estimate does not affect the existing scheduler.</p>
        </div>
      </section>
    </>
  )
}
