import { useEffect, useState } from 'react'
import { CaretLeft } from '@phosphor-icons/react'
import { listAllDoctors } from '../api'
import type { Doctor } from '../types'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import QueueSection from './QueueSection'

// Remembers the last doctor viewed here, per browser -- a pure
// per-viewer convenience (not shared/authoritative state, so
// localStorage is the right tool, not the artifact database this repo
// doesn't have anyway): staff/a doctor reopening this screen on the
// same machine during a shift shouldn't have to re-pick every time.
const LAST_DOCTOR_KEY = 'admin_queue_panel_last_doctor_id'

// A standalone, doctor-selectable version of DoctorDetail.tsx's Queue
// tab (same QueueSection component, not a second implementation) --
// the thing actually missing before this: reaching the queue required
// Doctors -> pick a doctor -> Queue tab. Not a top-level sidebar
// destination itself (AdminApp.tsx) -- reached from inside the
// Appointments/OPD workspace, either via its header "Queue" button (no
// doctor preselected, same as this panel's own default -- last-viewed
// doctor or the first active one) or a per-appointment "Open Queue"
// row action, meant to be the screen staff or a doctor leaves open
// during a shift (QueueSection polls on its own). onBack mirrors
// BookAppointmentPanel's onViewAppointments: the one way back to that
// workspace now that this isn't its own nav item.
export default function QueuePanel({
  onBack,
  onOpenConsultation,
}: {
  onBack: () => void
  onOpenConsultation?: (appointmentId: number) => void
}) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [doctorId, setDoctorId] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listAllDoctors()
      .then((list) => {
        const active = list.filter((d) => d.active)
        setDoctors(active)
        const remembered = localStorage.getItem(LAST_DOCTOR_KEY)
        const initial = remembered && active.some((d) => String(d.id) === remembered) ? remembered : String(active[0]?.id ?? '')
        setDoctorId(initial)
      })
      .catch(() => undefined)
      .finally(() => setLoading(false))
  }, [])

  function handleSelect(value: string) {
    setDoctorId(value)
    try {
      localStorage.setItem(LAST_DOCTOR_KEY, value)
    } catch {
      // Best-effort only -- a private window or blocked storage just
      // means the doctor picker won't remember next time, nothing else
      // breaks.
    }
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Queue</h2>
          <p className="muted">Today&apos;s walk-in queue -- who&apos;s up next for each doctor.</p>
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>
          <CaretLeft size={14} weight="bold" /> Back to Appointments
        </button>
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}

      {!loading && doctors.length === 0 && <p className="muted">No doctors yet.</p>}

      {!loading && doctors.length > 0 && (
        <>
          <label className="inline-label" style={{ marginBottom: 'var(--space-4)' }}>
            Doctor
            <Select value={doctorId} onValueChange={handleSelect}>
              <SelectTrigger className="filter-select-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {doctors.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>

          {doctorId && (
            <QueueSection key={doctorId} doctorId={Number(doctorId)} onOpenConsultation={onOpenConsultation} />
          )}
        </>
      )}
    </section>
  )
}
