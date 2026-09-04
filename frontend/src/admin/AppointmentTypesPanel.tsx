import { useEffect, useState } from 'react'
import { Tag } from '@phosphor-icons/react'
import { ApiError, createAppointmentType, listAppointmentTypeCatalog } from '../api'
import type { AppointmentTypeSummary } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../components/ui/alert-dialog'

export default function AppointmentTypesPanel({ isAdmin }: { isAdmin: boolean }) {
  const [types, setTypes] = useState<AppointmentTypeSummary[]>([])
  const [name, setName] = useState('')
  const [pendingName, setPendingName] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const tbodyRef = useStaggerReveal<HTMLTableSectionElement>([types])

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

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setPendingName(name.trim())
  }

  async function confirmCreate() {
    if (!pendingName) return
    setError(null)
    setBusy(true)
    try {
      await createAppointmentType(pendingName)
      setName('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create appointment type')
    } finally {
      setBusy(false)
      setPendingName(null)
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
        <form className="inline-form" onSubmit={handleSubmit}>
          <input
            placeholder="New appointment type name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button type="submit" style={{ width: 'auto' }} disabled={busy}>
            {busy ? 'Adding…' : 'Add type'}
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
            <Tag size={28} weight="light" />
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
          <tbody ref={tbodyRef}>
            {types.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <AlertDialog open={pendingName !== null} onOpenChange={(open) => !open && setPendingName(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Add this appointment type?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingName && `Add "${pendingName}" to the appointment type catalog?`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmCreate}>Save</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
