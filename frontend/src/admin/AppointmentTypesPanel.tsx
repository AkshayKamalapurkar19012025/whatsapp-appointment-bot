import { useEffect, useState } from 'react'
import { Tag, PencilSimple, Trash, X, Check } from '@phosphor-icons/react'
import {
  ApiError,
  createAppointmentType,
  deleteAppointmentType,
  listAppointmentTypeCatalog,
  updateAppointmentType,
} from '../api'
import type { AppointmentTypeSummary } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'

export default function AppointmentTypesPanel({ isAdmin }: { isAdmin: boolean }) {
  const [types, setTypes] = useState<AppointmentTypeSummary[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState('')
  const [deletingId, setDeletingId] = useState<number | null>(null)
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

  function startEdit(t: AppointmentTypeSummary) {
    setError(null)
    setEditingId(t.id)
    setEditingName(t.name)
  }

  function cancelEdit() {
    setEditingId(null)
    setEditingName('')
  }

  async function handleRename(e: React.FormEvent, appointmentTypeId: number) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await updateAppointmentType(appointmentTypeId, editingName)
      cancelEdit()
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update appointment type')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(t: AppointmentTypeSummary) {
    if (
      !window.confirm(
        `Remove "${t.name}"? Doctors offering it keep their appointment history, but it will no longer appear for booking.`,
      )
    ) {
      return
    }
    setError(null)
    setDeletingId(t.id)
    try {
      await deleteAppointmentType(t.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove appointment type')
    } finally {
      setDeletingId(null)
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
          {types.map((t) =>
            editingId === t.id ? (
              <form key={t.id} className="card-grid-item editing" onSubmit={(e) => handleRename(e, t.id)}>
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
              <div key={t.id} className="card-grid-item">
                <span>{t.name}</span>
                {isAdmin && (
                  <span className="card-grid-item-actions">
                    <button type="button" className="icon-btn" onClick={() => startEdit(t)} aria-label={`Edit ${t.name}`}>
                      <PencilSimple size={16} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn danger"
                      onClick={() => handleDelete(t)}
                      disabled={deletingId === t.id}
                      aria-label={`Remove ${t.name}`}
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
