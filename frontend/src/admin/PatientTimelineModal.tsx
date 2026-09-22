import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X, CalendarBlank } from '@phosphor-icons/react'
import { ApiError, getPatientTimeline } from '../api'
import type { Patient, PatientTimeline, TimelineVisit } from '../types'
import { formatDate, formatDateTime, formatTime } from '../format'

const APPOINTMENT_STATUS_LABELS: Record<string, string> = {
  PENDING: 'Pending',
  CONFIRMED: 'Confirmed',
  REJECTED: 'Rejected',
  CANCELLED: 'Cancelled',
  CHECKED_IN: 'Checked In',
  COMPLETED: 'Completed',
  NO_SHOW: 'No-Show',
}

function VisitCard({ visit, expanded, onToggle }: { visit: TimelineVisit; expanded: boolean; onToggle: () => void }) {
  return (
    <li>
      <button type="button" className="patient-timeline-visit-header" onClick={onToggle} aria-expanded={expanded}>
        <div>
          <strong>{formatDate(visit.started_at)}</strong>
          <span className="muted"> · {formatTime(visit.started_at)}</span>
        </div>
        <div className="patient-timeline-visit-header-right">
          <span className="muted">{visit.doctor_name}</span>
          {visit.appointment_status && (
            <span className={`pill status-${visit.appointment_status.toLowerCase()}`}>
              {APPOINTMENT_STATUS_LABELS[visit.appointment_status] ?? visit.appointment_status}
            </span>
          )}
          <span className={`pill status-${visit.status.toLowerCase()}`}>
            {visit.status === 'OPEN' ? 'In progress' : 'Closed'}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="patient-timeline-visit-body">
          {visit.token_number !== null && (
            <p className="muted">
              Token #{visit.token_number} · {visit.appointment_type_name}
            </p>
          )}

          <h4>Vitals</h4>
          {visit.vitals.length === 0 && <p className="muted">No vitals recorded.</p>}
          {visit.vitals.map((v) => (
            <div key={v.id} className="patient-timeline-vitals-row">
              <span className="muted">{formatDateTime(v.recorded_at)}</span>
              <span>
                {[
                  v.bp_systolic && v.bp_diastolic ? `BP ${v.bp_systolic}/${v.bp_diastolic}` : null,
                  v.pulse ? `Pulse ${v.pulse}` : null,
                  v.temperature_celsius ? `Temp ${v.temperature_celsius}°C` : null,
                  v.spo2 ? `SpO2 ${v.spo2}%` : null,
                  v.bmi ? `BMI ${v.bmi}` : null,
                ]
                  .filter(Boolean)
                  .join(' · ') || '—'}
              </span>
              {v.chief_complaint && <span className="muted">"{v.chief_complaint}"</span>}
            </div>
          ))}

          <h4>Consultation</h4>
          {!visit.consultation && <p className="muted">No consultation on record.</p>}
          {visit.consultation && (
            <div className="detail-section">
              <span className={`pill status-${visit.consultation.status.toLowerCase()}`}>
                {visit.consultation.status}
              </span>
              {visit.consultation.chief_complaint && (
                <p>
                  <strong>Chief complaint:</strong> {visit.consultation.chief_complaint}
                </p>
              )}
              {visit.consultation.diagnosis && (
                <p>
                  <strong>Diagnosis:</strong> {visit.consultation.diagnosis}
                </p>
              )}
              {visit.consultation.clinical_notes && (
                <p>
                  <strong>Notes:</strong> {visit.consultation.clinical_notes}
                </p>
              )}
              {visit.consultation.follow_up_date && (
                <p className="muted">Follow-up: {formatDate(visit.consultation.follow_up_date)}</p>
              )}
            </div>
          )}

          <h4>Orders{visit.orders.length > 0 ? ` (${visit.orders.length})` : ''}</h4>
          {visit.orders.length === 0 && <p className="muted">No orders placed.</p>}
          {visit.orders.length > 0 && (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Description</th>
                  <th>Status</th>
                  <th>Results</th>
                </tr>
              </thead>
              <tbody>
                {visit.orders.map((o) => (
                  <tr key={o.id}>
                    <td>{o.order_type}</td>
                    <td>{o.description}</td>
                    <td>
                      <span className={`pill status-${o.status.toLowerCase()}`}>{o.status}</span>
                    </td>
                    <td>
                      {o.results.length === 0
                        ? '—'
                        : o.results
                            .map((r) => `${r.parameter}: ${r.result_value}${r.unit ? ` ${r.unit}` : ''}`)
                            .join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h4>Prescription</h4>
          {!visit.prescription && <p className="muted">No prescription on record.</p>}
          {visit.prescription && (
            <>
              <span className={`pill status-${visit.prescription.status.toLowerCase()}`}>
                {visit.prescription.status}
              </span>
              {visit.prescription.items.length > 0 && (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Medicine</th>
                      <th>Dosage</th>
                      <th>Qty</th>
                      <th>Dispensed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visit.prescription.items.map((item) => (
                      <tr key={item.id}>
                        <td>{item.medicine_name}</td>
                        <td>{item.dosage || '—'}</td>
                        <td>{item.quantity}</td>
                        <td>{item.quantity_dispensed}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}

          <h4>Billing</h4>
          {!visit.invoice && <p className="muted">No bill on record.</p>}
          {visit.invoice && (
            <>
              <p className="muted">
                Bill {visit.invoice.invoice_number} · {visit.invoice.charges.length} charge
                {visit.invoice.charges.length === 1 ? '' : 's'} · {visit.invoice.payments.length} payment
                {visit.invoice.payments.length === 1 ? '' : 's'}
              </p>
              {visit.invoice.payments.length > 0 && (
                <p>
                  Paid: ₹
                  {visit.invoice.payments.reduce((sum, p) => sum + p.amount - p.refunded_amount, 0).toFixed(2)}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </li>
  )
}

// OPD/HIMS master spec Phase 10 (section 44) -- Patient 360 / unified
// timeline. Supersedes the old PatientVisitHistoryModal (a flat
// date/doctor/status list with no drill-down, see that file's own
// removed docstring) with the real cross-domain view the audit
// (docs/OPD_HIMS_P0_AUDIT.md section 5) flagged as missing: every visit
// for this patient, each expandable into everything that happened
// during it -- vitals, consultation, orders + results, prescription +
// dispensing, billing.
export default function PatientTimelineModal({ patient, onClose }: { patient: Patient; onClose: () => void }) {
  const [timeline, setTimeline] = useState<PatientTimeline | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    getPatientTimeline(patient.id)
      .then((result) => {
        if (cancelled) return
        setTimeline(result)
        // Most recent visit open by default -- the one someone opening
        // this modal almost always wants to see first.
        if (result.visits.length > 0) setExpandedId(result.visits[0].encounter_id)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load this patient\'s timeline')
      })
    return () => {
      cancelled = true
    }
  }, [patient.id])

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel patient-timeline-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Timeline for ${patient.name}`}
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">{patient.name}'s timeline</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          {patient.uhid} · {patient.whatsapp_number}
        </p>
        {timeline?.redirected_from && (
          <p className="muted">
            This patient's UHID {timeline.redirected_from.uhid} was merged into {timeline.patient_uhid} -- showing
            the combined timeline.
          </p>
        )}

        {error && <p className="error">{error}</p>}

        {!error && timeline === null && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading timeline…
          </div>
        )}

        {!error && timeline !== null && timeline.visits.length === 0 && (
          <div className="state-block empty">
            <span className="state-icon" aria-hidden="true">
              <CalendarBlank size={24} weight="light" />
            </span>
            No visits on record for this patient yet.
          </div>
        )}

        {!error && timeline !== null && timeline.visits.length > 0 && (
          <ul className="patient-visit-history-list">
            {timeline.visits.map((visit) => (
              <VisitCard
                key={visit.encounter_id}
                visit={visit}
                expanded={expandedId === visit.encounter_id}
                onToggle={() => setExpandedId(expandedId === visit.encounter_id ? null : visit.encounter_id)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>,
    document.body,
  )
}
