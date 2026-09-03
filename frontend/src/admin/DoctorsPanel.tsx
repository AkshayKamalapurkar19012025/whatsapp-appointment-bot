import { useEffect, useState } from 'react'
import { ApiError, createDoctor, listAllDoctors } from '../api'
import type { Doctor } from '../types'
import DoctorDetail from './DoctorDetail'

export default function DoctorsPanel({ isAdmin }: { isAdmin: boolean }) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [selectedDoctor, setSelectedDoctor] = useState<Doctor | null>(null)

  function load() {
    setLoading(true)
    listAllDoctors()
      .then(setDoctors)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctors'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const created = await createDoctor(name)
      setName('')
      load()
      setSelectedDoctor(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create doctor')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Doctors</h2>
      {error && <p className="error">{error}</p>}

      {isAdmin && (
        <form className="inline-form" onSubmit={handleCreate}>
          <input
            placeholder="New doctor name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button type="submit" disabled={busy}>
            {busy ? 'Creating…' : 'Add doctor'}
          </button>
        </form>
      )}

      {loading && <p>Loading…</p>}
      {!loading && doctors.length === 0 && <p className="muted">No doctors yet.</p>}

      <ul className="option-list">
        {doctors.map((d) => (
          <li key={d.id}>
            <button
              type="button"
              className={selectedDoctor?.id === d.id ? 'selected' : ''}
              onClick={() => setSelectedDoctor(d)}
            >
              {d.name}
            </button>
          </li>
        ))}
      </ul>

      {selectedDoctor && <DoctorDetail doctor={selectedDoctor} isAdmin={isAdmin} />}
    </section>
  )
}
