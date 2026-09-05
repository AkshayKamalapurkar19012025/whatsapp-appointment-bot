import { useEffect, useState } from 'react'
import { Buildings, PencilSimple, Trash, X, Check } from '@phosphor-icons/react'
import { ApiError, createDepartment, deleteDepartment, listDepartments, updateDepartment } from '../api'
import type { Department } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'

export default function DepartmentsPanel({ isAdmin }: { isAdmin: boolean }) {
  const [departments, setDepartments] = useState<Department[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState('')
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const gridRef = useStaggerReveal<HTMLDivElement>([departments])

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

  function startEdit(d: Department) {
    setError(null)
    setEditingId(d.id)
    setEditingName(d.name)
  }

  function cancelEdit() {
    setEditingId(null)
    setEditingName('')
  }

  async function handleRename(e: React.FormEvent, departmentId: number) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await updateDepartment(departmentId, editingName)
      cancelEdit()
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update department')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(d: Department) {
    if (!window.confirm(`Remove "${d.name}"? Doctors assigned to it keep their history, but it will no longer appear for booking.`)) {
      return
    }
    setError(null)
    setDeletingId(d.id)
    try {
      await deleteDepartment(d.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove department')
    } finally {
      setDeletingId(null)
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
        <div className="card-grid" ref={gridRef}>
          {departments.map((d) =>
            editingId === d.id ? (
              <form key={d.id} className="card-grid-item editing" onSubmit={(e) => handleRename(e, d.id)}>
                <input
                  autoFocus
                  value={editingName}
                  onChange={(e) => setEditingName(e.target.value)}
                  required
                />
                <span className="card-grid-item-actions">
                  <button type="submit" className="icon-btn" disabled={busy} aria-label="Save">
                    <Check size={16} />
                  </button>
                  <button type="button" className="icon-btn" onClick={cancelEdit} aria-label="Cancel">
                    <X size={16} />
                  </button>
                </span>
              </form>
            ) : (
              <div key={d.id} className="card-grid-item">
                <span>{d.name}</span>
                {isAdmin && (
                  <span className="card-grid-item-actions">
                    <button type="button" className="icon-btn" onClick={() => startEdit(d)} aria-label={`Edit ${d.name}`}>
                      <PencilSimple size={16} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn danger"
                      onClick={() => handleDelete(d)}
                      disabled={deletingId === d.id}
                      aria-label={`Remove ${d.name}`}
                    >
                      <Trash size={16} />
                    </button>
                  </span>
                )}
              </div>
            ),
          )}
        </div>
      )}
    </section>
  )
}
