import { useEffect, useState } from 'react'
import { ApiError, createDepartment, listDepartments } from '../api'
import type { Department } from '../types'

export default function DepartmentsPanel({ isAdmin }: { isAdmin: boolean }) {
  const [departments, setDepartments] = useState<Department[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  function load() {
    setLoading(true)
    listDepartments()
      .then(setDepartments)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load departments'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await createDepartment(name)
      setName('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create department')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Departments</h2>
      {error && <p className="error">{error}</p>}

      {isAdmin && (
        <form className="inline-form" onSubmit={handleCreate}>
          <input
            placeholder="New department name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button type="submit" disabled={busy}>
            {busy ? 'Creating…' : 'Add department'}
          </button>
        </form>
      )}

      {loading && <p>Loading…</p>}
      {!loading && departments.length === 0 && <p className="muted">No departments yet.</p>}

      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
          </tr>
        </thead>
        <tbody>
          {departments.map((d) => (
            <tr key={d.id}>
              <td>{d.name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
