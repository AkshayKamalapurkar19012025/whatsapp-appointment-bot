import { useEffect, useState } from 'react'
import { listDepartments, listDoctorsInDepartment } from '../api'
import type { Department, Doctor } from '../types'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import QueueSection from './QueueSection'
import type { ConsultationTab } from './ConsultationWorkspace'

// master spec audit "subsequent gaps" list (screen 23, "Department
// Queue... Not found as its own view"): a department-wide view of
// today's walk-in queue, one section per doctor in the department.
// Deliberately reuses QueueSection as-is (the same component
// QueuePanel.tsx renders for a single doctor) rather than a second
// queue implementation or a new backend aggregation endpoint -- one
// QueueSection instance per doctor, each polling and acting on its own
// doctor's queue exactly the way it already does everywhere else this
// component is used.
export default function DepartmentQueuePanel({
  onOpenConsultation,
}: {
  onOpenConsultation?: (appointmentId: number, tab?: ConsultationTab) => void
}) {
  const [departments, setDepartments] = useState<Department[]>([])
  const [departmentId, setDepartmentId] = useState('')
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingDoctors, setLoadingDoctors] = useState(false)

  useEffect(() => {
    listDepartments()
      .then((list) => {
        const active = list.filter((d) => d.active)
        setDepartments(active)
        setDepartmentId(String(active[0]?.id ?? ''))
      })
      .catch(() => undefined)
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!departmentId) {
      setDoctors([])
      return
    }
    let cancelled = false
    setLoadingDoctors(true)
    listDoctorsInDepartment(Number(departmentId))
      .then((list) => {
        if (!cancelled) setDoctors(list.filter((d) => d.active))
      })
      .catch(() => {
        if (!cancelled) setDoctors([])
      })
      .finally(() => {
        if (!cancelled) setLoadingDoctors(false)
      })
    return () => {
      cancelled = true
    }
  }, [departmentId])

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Department Queue</h2>
          <p className="muted">Today&apos;s walk-in queue for every doctor in a department, at a glance.</p>
        </div>
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}

      {!loading && departments.length === 0 && <p className="muted">No departments yet.</p>}

      {!loading && departments.length > 0 && (
        <>
          <label className="inline-label" style={{ marginBottom: 'var(--space-4)' }}>
            Department
            <Select value={departmentId} onValueChange={setDepartmentId}>
              <SelectTrigger className="filter-select-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {departments.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>

          {loadingDoctors && (
            <div className="state-block">
              <span className="spinner" aria-hidden="true" />
              Loading doctors…
            </div>
          )}

          {!loadingDoctors && departmentId && doctors.length === 0 && (
            <div className="state-block empty">No active doctors in this department.</div>
          )}

          {!loadingDoctors &&
            doctors.map((d) => (
              <div key={d.id} className="department-queue-doctor-section">
                <h3>{d.name}</h3>
                <QueueSection doctorId={d.id} onOpenConsultation={onOpenConsultation} />
              </div>
            ))}
        </>
      )}
    </section>
  )
}
