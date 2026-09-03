import { useEffect, useState } from 'react'
import { ApiError, createPatientAdmin, listPatients } from '../api'
import type { Patient } from '../types'
import PhoneInput from '../PhoneInput'

export default function PatientsPanel() {
  const [patients, setPatients] = useState<Patient[]>([])
  const [name, setName] = useState('')
  const [whatsappNumber, setWhatsappNumber] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  function load() {
    setLoading(true)
    listPatients()
      .then(setPatients)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load patients'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await createPatientAdmin(name, whatsappNumber)
      setName('')
      setWhatsappNumber('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create patient')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Patients</h2>
      <p className="muted">
        A patient record can also be created here directly (e.g. registering someone over the
        phone) -- the same table WhatsApp and the patient web login write to.
      </p>
      {error && <p className="error">{error}</p>}

      <form className="inline-form wrap" onSubmit={handleCreate}>
        <label className="inline-label">
          Name
          <input placeholder="Full name" value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="inline-label">
          Mobile number
          <PhoneInput value={whatsappNumber} onChange={setWhatsappNumber} />
        </label>
        <button type="submit" style={{ width: 'auto' }} disabled={busy}>
          {busy ? 'Creating…' : 'Add patient'}
        </button>
      </form>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading patients…
        </div>
      )}
      {!loading && patients.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            🧑‍⚕️
          </span>
          No patients yet.
        </div>
      )}

      {!loading && patients.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>WhatsApp number</th>
            </tr>
          </thead>
          <tbody>
            {patients.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td>{p.whatsapp_number}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
