import { useEffect, useState } from 'react'
import { ApiError, createPatientAdmin, listPatients } from '../api'
import type { Patient } from '../types'

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

      <form className="inline-form" onSubmit={handleCreate}>
        <input
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <input
          placeholder="+91 98765 43210"
          value={whatsappNumber}
          onChange={(e) => setWhatsappNumber(e.target.value)}
          required
        />
        <button type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Add patient'}
        </button>
      </form>

      {loading && <p>Loading…</p>}
      {!loading && patients.length === 0 && <p className="muted">No patients yet.</p>}

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
    </section>
  )
}
