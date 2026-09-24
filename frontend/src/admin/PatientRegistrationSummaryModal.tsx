import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import { ApiError, getPatient } from '../api'
import type { Patient } from '../types'
import { formatAgeGender, formatDate, formatDateTime } from '../format'

// Printing phase section 1 -- the Patient Registration Summary. Same
// shape as PaymentReceiptModal.tsx: fetch on open, .print-area (
// styles.css's shared @media print rule) scopes what actually comes out
// of "Print"/"Download", both still the browser's own print dialog (see
// that modal's own comment for why a second, server-rendered PDF path
// wasn't built here either).
//
// Fetches GET /patients/{id} rather than reusing the row already in
// PatientsPanel's table, because that row (listPatientsAdmin) omits the
// address/emergency-contact/blood-group columns this document needs --
// see that endpoint's own comment.
export default function PatientRegistrationSummaryModal({
  patientId,
  onClose,
}: {
  patientId: number
  onClose: () => void
}) {
  const [patient, setPatient] = useState<Patient | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getPatient(patientId)
      .then((result) => {
        if (!cancelled) setPatient(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load this patient')
      })
    return () => {
      cancelled = true
    }
  }, [patientId])

  const address = patient
    ? [patient.address_line, patient.city, patient.state, patient.pincode].filter(Boolean).join(', ')
    : ''

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel receipt-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Patient registration summary"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        {error && <p className="error">{error}</p>}

        {!error && patient === null && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading patient…
          </div>
        )}

        {patient && (
          <>
            <div className="print-area registration-print-area">
              <h3 className="appointment-details-heading">Patient Registration Summary</h3>
              <p className="muted" style={{ marginTop: 0 }}>
                UHID {patient.uhid}
              </p>

              <table className="data-table">
                <tbody>
                  <tr>
                    <td>Patient name</td>
                    <td>
                      <strong>{patient.name}</strong>
                    </td>
                  </tr>
                  <tr>
                    <td>UHID</td>
                    <td>{patient.uhid}</td>
                  </tr>
                  <tr>
                    <td>Date of birth</td>
                    <td>{patient.date_of_birth ? formatDate(patient.date_of_birth) : '—'}</td>
                  </tr>
                  <tr>
                    <td>Age / Gender</td>
                    <td>{formatAgeGender(patient.date_of_birth, patient.gender) ?? '—'}</td>
                  </tr>
                  <tr>
                    <td>Mobile</td>
                    <td>{patient.whatsapp_number}</td>
                  </tr>
                  <tr>
                    <td>Address</td>
                    <td>{address || '—'}</td>
                  </tr>
                  <tr>
                    <td>Emergency contact</td>
                    <td>
                      {patient.emergency_contact_name || patient.emergency_contact_phone
                        ? [patient.emergency_contact_name, patient.emergency_contact_phone].filter(Boolean).join(' · ')
                        : '—'}
                    </td>
                  </tr>
                  <tr>
                    <td>Registration date</td>
                    <td>{patient.registered_at ? formatDateTime(patient.registered_at) : '—'}</td>
                  </tr>
                  <tr>
                    <td>Registration number</td>
                    <td>{patient.uhid}</td>
                  </tr>
                </tbody>
              </table>
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
