import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import { ApiError, getAppointmentSlip } from '../api'
import type { AppointmentSlip } from '../types'
import { formatDate, formatTime } from '../format'

// Printing phase section 3 -- the OPD Appointment Slip. Deliberately
// out of scope in the earlier print-layouts pass (see that commit's
// message) for lack of a concrete field list; the Printing phase spec
// now gives one, so this closes that gap the same way the token/
// prescription/bill/requisition print views were built: a .print-area
// (styles.css) scoping what "Print"/"Download" (the browser's own print
// dialog, same stance as every other print view here) actually outputs.
//
// "Instructions"/"appointment desk" copy is static text, not a backend
// field -- the same choice BookAppointmentPanel's token slip already
// made for its own "Please wait for your token to be called" line, since
// there's no hospital-configurable instructions column to source it
// from yet (Printing phase section 21, not built here).
export default function AppointmentSlipModal({ appointmentId, onClose }: { appointmentId: number; onClose: () => void }) {
  const [slip, setSlip] = useState<AppointmentSlip | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getAppointmentSlip(appointmentId)
      .then((result) => {
        if (!cancelled) setSlip(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load this appointment slip')
      })
    return () => {
      cancelled = true
    }
  }, [appointmentId])

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel receipt-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Appointment slip"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        {error && <p className="error">{error}</p>}

        {!error && slip === null && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading appointment slip…
          </div>
        )}

        {slip && (
          <>
            <div className="print-area slip-print-area">
              <h3 className="appointment-details-heading">{slip.hospital_name}</h3>
              <p className="muted" style={{ marginTop: 0 }}>
                Appointment {slip.appointment_number}
              </p>

              <table className="data-table">
                <tbody>
                  <tr>
                    <td>Patient</td>
                    <td>
                      <strong>{slip.patient_name}</strong> ({slip.patient_uhid})
                    </td>
                  </tr>
                  <tr>
                    <td>Department</td>
                    <td>{slip.department_name ?? '—'}</td>
                  </tr>
                  <tr>
                    <td>Doctor</td>
                    <td>{slip.doctor_name}</td>
                  </tr>
                  <tr>
                    <td>Date</td>
                    <td>{formatDate(slip.start_at)}</td>
                  </tr>
                  <tr>
                    <td>Time</td>
                    <td>
                      {formatTime(slip.start_at)} – {formatTime(slip.end_at)}
                    </td>
                  </tr>
                  <tr>
                    <td>Visit type</td>
                    <td>{slip.visit_type}</td>
                  </tr>
                  <tr>
                    <td>Contact</td>
                    <td>{slip.patient_contact}</td>
                  </tr>
                </tbody>
              </table>

              <p className="muted">
                Please arrive 15 minutes before your appointment time with this slip and any previous
                records. For queries, contact the appointment desk at {slip.hospital_name}.
              </p>
            </div>

            <div className="doctor-quick-actions receipt-actions">
              <button type="button" className="btn btn-sm" onClick={() => window.print()}>
                Print / Download
              </button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
