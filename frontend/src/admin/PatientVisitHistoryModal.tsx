import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X, CalendarBlank } from '@phosphor-icons/react'
import { ApiError, listAdminAppointments } from '../api'
import type { AdminAppointment, Patient } from '../types'
import { formatDate, formatTime } from '../format'

const STATUS_LABELS: Record<string, string> = {
  PENDING: 'Pending',
  CONFIRMED: 'Confirmed',
  REJECTED: 'Rejected',
  CANCELLED: 'Cancelled',
  CHECKED_IN: 'Checked In',
  COMPLETED: 'Completed',
  NO_SHOW: 'No-Show',
}

// PatientsPanel's "N visits" count is otherwise a dead end -- a bare
// number with no way to see when or with which doctor (the Patients
// directory itself, unlike AppointmentsPanel, was never meant to
// double as an appointment browser -- see that panel's own module
// docstring). This is the minimal, self-contained answer: every
// appointment for this one patient, unbounded by date, fetched
// directly (GET /appointments?patient_id=) rather than routed through
// AppointmentsPanel's date-scoped/queue-aware machinery, which has no
// "show me one patient's whole history regardless of date" mode and
// isn't worth bending into one for a single read-only list.
export default function PatientVisitHistoryModal({ patient, onClose }: { patient: Patient; onClose: () => void }) {
  const [appointments, setAppointments] = useState<AdminAppointment[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    listAdminAppointments({ patient_id: patient.id })
      .then((list) => {
        if (cancelled) return
        setAppointments([...list].sort((a, b) => b.start_at.localeCompare(a.start_at)))
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load visit history')
      })
    return () => {
      cancelled = true
    }
  }, [patient.id])

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`Visit history for ${patient.name}`}
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">{patient.name}'s visit history</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          {patient.uhid} · {patient.whatsapp_number}
        </p>

        {error && <p className="error">{error}</p>}

        {!error && appointments === null && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading visit history…
          </div>
        )}

        {!error && appointments !== null && appointments.length === 0 && (
          <div className="state-block empty">
            <span className="state-icon" aria-hidden="true">
              <CalendarBlank size={24} weight="light" />
            </span>
            No appointments on record for this patient yet.
          </div>
        )}

        {!error && appointments !== null && appointments.length > 0 && (
          <ul className="patient-visit-history-list">
            {appointments.map((a) => (
              <li key={a.id}>
                <div className="patient-visit-history-row">
                  <div>
                    <strong>{formatDate(a.start_at)}</strong>
                    <span className="muted"> · {formatTime(a.start_at)}</span>
                  </div>
                  <span className={`pill status-${a.status.toLowerCase()}`}>
                    {STATUS_LABELS[a.status] ?? a.status}
                  </span>
                </div>
                <div className="muted patient-visit-history-meta">
                  {a.doctor_name} · {a.appointment_type_name}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>,
    document.body,
  )
}
