import { useEffect, useState } from 'react'
import { Stethoscope } from '@phosphor-icons/react'
import { ApiError, createDoctor, listAllDoctors, listDepartments, listDoctorsInDepartment } from '../api'
import type { Department, Doctor } from '../types'
import { formatDateTime } from '../format'
import { useStaggerReveal } from '../useStaggerReveal'
import DoctorDetail from './DoctorDetail'
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

interface DoctorGroup {
  key: string
  label: string
  doctors: Doctor[]
}

export default function DoctorsPanel({ isAdmin }: { isAdmin: boolean }) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [groups, setGroups] = useState<DoctorGroup[]>([])
  const [name, setName] = useState('')
  const [pendingName, setPendingName] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [selectedDoctor, setSelectedDoctor] = useState<Doctor | null>(null)
  const listRef = useStaggerReveal<HTMLDivElement>([groups], '.doctor-card')

  // Doctors have no department field of their own (see types.ts) -- the
  // relationship is doctor<->department, many-to-many, so grouping for
  // display means fetching each department's own doctor list and
  // bucketing by that rather than reading a column off Doctor itself.
  // Any doctor absent from every department's list falls into its own
  // "Unassigned" bucket so it isn't silently dropped from the grid.
  function load() {
    setLoading(true)
    setError(null)
    Promise.all([listAllDoctors(), listDepartments()])
      .then(async ([allDoctors, departments]) => {
        setDoctors(allDoctors)
        const perDepartment = await Promise.all(
          departments.map((d) => listDoctorsInDepartment(d.id).catch(() => [] as Doctor[])),
        )
        const assignedIds = new Set<number>()
        const builtGroups: DoctorGroup[] = departments
          .map((d: Department, i: number) => {
            for (const doc of perDepartment[i]) assignedIds.add(doc.id)
            return { key: `dept-${d.id}`, label: d.name, doctors: perDepartment[i] }
          })
          .filter((g) => g.doctors.length > 0)
        const unassigned = allDoctors.filter((d) => !assignedIds.has(d.id))
        if (unassigned.length > 0) {
          builtGroups.push({ key: 'unassigned', label: 'Unassigned', doctors: unassigned })
        }
        setGroups(builtGroups)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctors'))
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
      const created = await createDoctor(pendingName)
      setName('')
      load()
      setSelectedDoctor(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create doctor')
    } finally {
      setBusy(false)
      setPendingName(null)
    }
  }

  return (
    <section>
      <h2>Doctors</h2>
      {error && <p className="error">{error}</p>}

      {isAdmin && (
        <form className="inline-form" onSubmit={handleSubmit}>
          <input
            placeholder="New doctor name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button type="submit" style={{ width: 'auto' }} disabled={busy}>
            {busy ? 'Adding…' : 'Add doctor'}
          </button>
        </form>
      )}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading doctors…
        </div>
      )}
      {!loading && doctors.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <Stethoscope size={28} weight="light" />
          </span>
          No doctors yet.
        </div>
      )}

      {!loading && groups.length > 0 && (
        <div ref={listRef}>
          {groups.map((group) => (
            <div key={group.key} className="doctor-group">
              <h4 className="doctor-group-label">{group.label}</h4>
              <div className="doctor-grid">
                {group.doctors.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    className={selectedDoctor?.id === d.id ? 'doctor-card selected' : 'doctor-card'}
                    onClick={() => setSelectedDoctor(d)}
                  >
                    <span>{d.name}</span>
                    <span className="muted doctor-added-meta">
                      Added {formatDateTime(d.created_at)}
                      {d.created_by ? ` by ${d.created_by}` : ''}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedDoctor && <DoctorDetail doctor={selectedDoctor} isAdmin={isAdmin} />}

      <AlertDialog open={pendingName !== null} onOpenChange={(open) => !open && setPendingName(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Add this doctor?</AlertDialogTitle>
            <AlertDialogDescription>{pendingName && `Add "${pendingName}" to the doctor list?`}</AlertDialogDescription>
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
