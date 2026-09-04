import { useEffect, useState } from 'react'
import { ApiError, createAppointmentType, listAppointmentTypeCatalog } from '../api'
import type { AppointmentTypeSummary } from '../types'

export default function AppointmentTypesPanel({ isAdmin }: { isAdmin: boolean }) {
  const [types, setTypes] = useState<AppointmentTypeSummary[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  function load() {
    setLoading(true)
    listAppointmentTypeCatalog()
      .then(setTypes)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : 'Could not load appointment types'),
      )
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await createAppointmentType(name)
      setName('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create appointment type')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Appointment Types</h2>
      <p className="muted">
        This is the catalog of appointment type names. Duration and pricing are set per doctor
        under Doctors → assign appointment type.
      </p>
      {error && <p className="error">{error}</p>}

      {isAdmin && (
        <form className="inline-form" onSubmit={handleCreate}>
          <input
            placeholder="New appointment type name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button type="submit" style={{ width: 'auto' }} disabled={busy}>
            {busy ? 'Creating…' : 'Add type'}
          </button>
        </form>
      )}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading appointment types…
        </div>
      )}
      {!loading && types.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            🏷️
          </span>
          No appointment types yet.
        </div>
      )}

      {!loading && types.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
            </tr>
          </thead>
          <tbody>
            {types.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
