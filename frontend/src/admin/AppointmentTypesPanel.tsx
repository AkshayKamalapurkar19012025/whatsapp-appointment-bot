import { useEffect, useState } from 'react'
import { Tag } from '@phosphor-icons/react'
import { ApiError, createAppointmentType, listAppointmentTypeCatalog } from '../api'
import type { AppointmentTypeSummary } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'

export default function AppointmentTypesPanel({ isAdmin }: { isAdmin: boolean }) {
  const [types, setTypes] = useState<AppointmentTypeSummary[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const gridRef = useStaggerReveal<HTMLDivElement>([types])

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
            {busy ? 'Saving…' : 'Save'}
          </button>
          {name && (
            <button type="button" className="btn-secondary btn" style={{ width: 'auto' }} onClick={() => setName('')}>
              Cancel
            </button>
          )}
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
            <Tag size={28} weight="light" />
          </span>
          No appointment types yet.
        </div>
      )}

      {!loading && types.length > 0 && (
        <div className="card-grid" ref={gridRef}>
          {types.map((t) => (
            <div key={t.id} className="card-grid-item">
              {t.name}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
