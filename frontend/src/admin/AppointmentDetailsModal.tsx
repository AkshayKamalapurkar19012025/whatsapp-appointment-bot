import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import type { AdminAppointment } from '../types'
import { formatDate, formatDateTime, formatTime } from '../format'
import { AppointmentActionButtons, buildAppointmentActions, type AppointmentActionHandlers } from './AppointmentActions'

// A read-only summary of exactly the fields AdminAppointment actually
// carries -- no department or booking source, since the admin
// appointments listing doesn't return them (see AdminAppointment
// in types.ts) and inventing placeholder data for fields the backend
// doesn't provide isn't the goal here. Ported to document.body via
// createPortal for the same reason DoctorProfileModal.tsx is: this
// panel is opened from inside .card/.admin-shell, both of which set
// backdrop-filter for their glass look, which becomes the containing
// block for any position:fixed descendant per spec -- without the
// portal this modal renders wherever the table happens to be scrolled
// to instead of as a true centered overlay.
export default function AppointmentDetailsModal({
  appointment,
  onClose,
  handlers,
  busy,
}: {
  appointment: AdminAppointment
  onClose: () => void
  handlers: AppointmentActionHandlers
  busy: boolean
}) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  // "View details" itself is meaningless as an action from inside the
  // details view it opens -- filtered out here rather than taught to
  // buildAppointmentActions, which still needs to offer it everywhere
  // else (the table row, the mobile card).
  const rawActions = buildAppointmentActions(appointment, handlers)
  const actions = { ...rawActions, overflow: rawActions.overflow.filter((item) => item.key !== 'details') }

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel appointment-details-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Appointment details"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">Appointment details</h3>

        <div className="appointment-details-block">
          <strong>{appointment.patient_name}</strong>
          <span className="muted">{appointment.whatsapp_number}</span>
        </div>

        <div className="appointment-details-block">
          <strong>{appointment.doctor_name}</strong>
          <span className="muted">{appointment.appointment_type_name}</span>
        </div>

        <div className="appointment-details-block">
          <span>{formatDate(appointment.start_at)}</span>
          <span className="muted">
            {formatTime(appointment.start_at)} – {formatTime(appointment.end_at)}
          </span>
        </div>

        <div className="appointment-details-block appointment-details-status">
          <span className={`pill status-${appointment.status.toLowerCase()}`}>
            {appointment.status.replace(/_/g, ' ')}
          </span>
          {appointment.token_number !== null && <span className="pill token-pill">Token #{appointment.token_number}</span>}
        </div>

        <p className="muted appointment-details-booked-on">Booked on {formatDateTime(appointment.created_at)}</p>

        <div className="appointment-details-actions">
          <AppointmentActionButtons actions={actions} busy={busy} />
        </div>
      </div>
    </div>,
    document.body,
  )
}
