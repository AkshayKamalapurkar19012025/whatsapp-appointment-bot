import { useEffect, useState } from 'react'
import { Buildings } from '@phosphor-icons/react'
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
          <button type="submit" style={{ width: 'auto' }} disabled={busy}>
            {busy ? 'Creating…' : 'Add department'}
          </button>
        </form>
      )}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading departments…
        </div>
      )}
      {!loading && departments.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <Buildings size={28} weight="light" />
          </span>
          No departments yet.
        </div>
      )}

      {!loading && departments.length > 0 && (
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
      )}
    </section>
  )
}
