import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import type { AdminAppointment, Invoice } from '../types'
import {
  addInvoiceLineItem,
  ApiError,
  getAppointmentInvoice,
  recordAppointmentPayment,
  refundAppointmentPayment,
  waiveAppointmentPayment,
} from '../api'
import { describeArrival, formatDate, formatDateTime, formatTime } from '../format'
import { AppointmentActionButtons, buildAppointmentActions, type AppointmentActionHandlers } from './AppointmentActions'

const PAYMENT_METHODS = ['CASH', 'UPI', 'CARD', 'OTHER'] as const

// The front-desk payment/waiver workflow (patient arrival workflow
// Phases 3-5): shown once a patient has been checked in, since payment
// isn't a relevant concept before then. Renders its own mini-forms
// (method choice, waiver reason) rather than relying on
// AppointmentActionButtons' generic "Collect Payment"/"Waive Charge"
// buttons -- those still exist for the table row/mobile card (where
// clicking them just opens this modal), but inside the modal itself
// they'd be redundant with the real form below, so the parent
// filters them out of the action set it passes in here (see
// AppointmentDetailsModal's own body).
//
// Keeps a local `override` of the payment-related fields rather than
// waiting for the parent's next list refresh to reflect a successful
// call -- onUpdated() still triggers that refresh in the background
// (so the table/mobile card stay in sync), but the modal itself
// updates immediately.
function PaymentSection({
  appointment,
  isAdmin,
  onUpdated,
}: {
  appointment: AdminAppointment
  isAdmin: boolean
  onUpdated: () => void
}) {
  const [override, setOverride] = useState<Partial<AdminAppointment> | null>(null)
  const [method, setMethod] = useState<(typeof PAYMENT_METHODS)[number]>('CASH')
  const [waiveReason, setWaiveReason] = useState('')
  const [showWaiveForm, setShowWaiveForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [justIssuedToken, setJustIssuedToken] = useState<number | null>(null)

  // The itemized bill (migrations/0026) -- fetched whenever this
  // section is showing at all, not just while a charge is still
  // collectible, so a PAID/REFUNDED visit that had ad-hoc charges added
  // before settlement still shows what actually made up the total.
  const [invoice, setInvoice] = useState<Invoice | null>(null)
  const [showLineItemForm, setShowLineItemForm] = useState(false)
  const [lineItemDescription, setLineItemDescription] = useState('')
  const [lineItemAmount, setLineItemAmount] = useState('')
  const [lineItemBusy, setLineItemBusy] = useState(false)
  const [lineItemError, setLineItemError] = useState<string | null>(null)

  const [showRefundForm, setShowRefundForm] = useState(false)
  const [refundAmount, setRefundAmount] = useState('')
  const [refundReason, setRefundReason] = useState('')
  const [refundBusy, setRefundBusy] = useState(false)
  const [refundError, setRefundError] = useState<string | null>(null)

  const current = { ...appointment, ...override }
  const sectionApplies = current.status === 'CHECKED_IN' || current.status === 'COMPLETED'

  useEffect(() => {
    if (!sectionApplies) return
    let cancelled = false
    getAppointmentInvoice(appointment.id)
      .then((result) => {
        if (!cancelled) setInvoice(result)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
    // Re-fetches on every payment_status change (a line item add, a
    // payment, a refund all move it) so the breakdown never goes stale
    // without a manual refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appointment.id, sectionApplies, current.payment_status])

  if (!sectionApplies) return null

  async function submitLineItem(e: React.FormEvent) {
    e.preventDefault()
    setLineItemError(null)
    const amount = Number(lineItemAmount)
    if (!lineItemDescription.trim() || !(amount > 0)) return
    setLineItemBusy(true)
    try {
      const result = await addInvoiceLineItem(appointment.id, lineItemDescription.trim(), amount)
      setInvoice(result)
      setLineItemDescription('')
      setLineItemAmount('')
      setShowLineItemForm(false)
    } catch (err) {
      setLineItemError(err instanceof ApiError ? err.message : 'Could not add this charge')
    } finally {
      setLineItemBusy(false)
    }
  }

  async function submitRefund(e: React.FormEvent) {
    e.preventDefault()
    setRefundError(null)
    const amount = Number(refundAmount)
    if (!refundReason.trim() || !(amount > 0)) return
    setRefundBusy(true)
    try {
      const result = await refundAppointmentPayment(appointment.id, amount, refundReason.trim())
      setOverride({
        payment_status: result.payment_status,
        refund_amount: result.refund_amount,
        refund_reason: result.refund_reason,
        refunded_at: result.refunded_at,
      })
      setShowRefundForm(false)
      onUpdated()
    } catch (err) {
      setRefundError(err instanceof ApiError ? err.message : 'Could not record the refund')
    } finally {
      setRefundBusy(false)
    }
  }

  async function submitPayment(outcome: 'PAID' | 'FAILED') {
    setError(null)
    setBusy(true)
    try {
      const result = await recordAppointmentPayment(appointment.id, method, outcome)
      setOverride({
        payment_status: result.payment_status,
        payment_method: result.payment_method,
        payment_amount: result.payment_amount,
        paid_at: result.payment_recorded_at,
        token_number: result.token_number,
      })
      if (result.token_just_issued) setJustIssuedToken(result.token_number)
      onUpdated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record the payment')
    } finally {
      setBusy(false)
    }
  }

  async function submitWaiver(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const result = await waiveAppointmentPayment(appointment.id, waiveReason)
      setOverride({
        payment_status: result.payment_status,
        payment_method: result.payment_method,
        payment_amount: result.payment_amount,
        paid_at: result.payment_recorded_at,
        waive_reason: result.waive_reason,
        token_number: result.token_number,
      })
      if (result.token_just_issued) setJustIssuedToken(result.token_number)
      setShowWaiveForm(false)
      onUpdated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not waive the consultation fee')
    } finally {
      setBusy(false)
    }
  }

  const needsAction = current.payment_status === 'UNPAID' || current.payment_status === 'FAILED'

  return (
    <div className="detail-section payment-section">
      <h4>Payment</h4>
      {error && <p className="error">{error}</p>}

      {justIssuedToken !== null && (
        <p className="success">
          {current.payment_status === 'WAIVED' ? 'Fee waived.' : 'Payment successful.'} Queue Token:{' '}
          <strong>{justIssuedToken}</strong>. Status: Waiting for Doctor.
        </p>
      )}

      {invoice && invoice.line_items.length > 0 ? (
        <>
          <div className="payment-summary-row">
            <span>Consultation Fee</span>
            <span>₹{invoice.consultation_fee}</span>
          </div>
          {invoice.line_items.map((item) => (
            <div className="payment-summary-row" key={item.id}>
              <span className="muted">{item.description}</span>
              <span>₹{item.amount}</span>
            </div>
          ))}
          <div className="payment-summary-row">
            <span>Total Due</span>
            <strong>₹{invoice.total_due}</strong>
          </div>
        </>
      ) : (
        <div className="payment-summary-row">
          <span>Consultation Fee</span>
          <strong>₹{appointment.consultation_fee}</strong>
        </div>
      )}
      <div className="payment-summary-row">
        <span>Payment Status</span>
        <span className={`pill payment-${current.payment_status.toLowerCase()}`}>
          {current.payment_status === 'PAID' && current.payment_method
            ? `Paid · ${current.payment_method}`
            : current.payment_status === 'WAIVED'
              ? 'Waived'
              : current.payment_status.replace(/_/g, ' ')}
        </span>
      </div>
      {current.payment_status === 'WAIVED' && current.waive_reason && (
        <p className="muted payment-waive-reason">Reason: {current.waive_reason}</p>
      )}
      {current.payment_status === 'REFUNDED' && current.refund_reason && (
        <p className="muted payment-waive-reason">
          Refunded ₹{current.refund_amount} · {current.refund_reason}
          {current.refunded_at && ` · ${formatDateTime(current.refunded_at)}`}
        </p>
      )}

      {needsAction && (
        <div className="payment-form">
          {isAdmin && !showLineItemForm && (
            <button type="button" className="link" onClick={() => setShowLineItemForm(true)}>
              + Add charge
            </button>
          )}
          {isAdmin && showLineItemForm && (
            <form className="inline-form wrap" onSubmit={submitLineItem}>
              {lineItemError && <p className="error">{lineItemError}</p>}
              <label className="inline-label">
                Description
                <input
                  value={lineItemDescription}
                  onChange={(e) => setLineItemDescription(e.target.value)}
                  placeholder="e.g. Dressing charge"
                  required
                />
              </label>
              <label className="inline-label">
                Amount
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={lineItemAmount}
                  onChange={(e) => setLineItemAmount(e.target.value)}
                  required
                />
              </label>
              <button type="submit" className="btn btn-sm" disabled={lineItemBusy || !lineItemDescription.trim() || !(Number(lineItemAmount) > 0)}>
                {lineItemBusy ? 'Adding…' : 'Add charge'}
              </button>
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setShowLineItemForm(false)}>
                Cancel
              </button>
            </form>
          )}

          {!showWaiveForm && (
            <>
              <label className="inline-label">
                Method
                <select value={method} onChange={(e) => setMethod(e.target.value as typeof method)}>
                  {PAYMENT_METHODS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <div className="payment-form-actions">
                <button type="button" className="btn btn-sm" disabled={busy} onClick={() => submitPayment('PAID')}>
                  {busy ? 'Recording…' : 'Record Payment'}
                </button>
                <button
                  type="button"
                  className="btn-secondary btn-outline-danger btn btn-sm"
                  disabled={busy}
                  onClick={() => submitPayment('FAILED')}
                >
                  Payment Failed
                </button>
                {isAdmin && (
                  <button
                    type="button"
                    className="btn-secondary btn btn-sm"
                    disabled={busy}
                    onClick={() => setShowWaiveForm(true)}
                  >
                    Waive Charge
                  </button>
                )}
              </div>
            </>
          )}

          {showWaiveForm && (
            <form className="inline-form wrap" onSubmit={submitWaiver}>
              <label className="inline-label" style={{ width: '100%' }}>
                Reason for waiving
                <textarea
                  value={waiveReason}
                  onChange={(e) => setWaiveReason(e.target.value)}
                  placeholder="e.g. Follow-up visit within 3 days"
                  required
                  rows={2}
                />
              </label>
              <button type="submit" className="btn btn-sm" disabled={busy || !waiveReason.trim()}>
                {busy ? 'Saving…' : 'Confirm Waiver'}
              </button>
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setShowWaiveForm(false)}>
                Cancel
              </button>
            </form>
          )}
        </div>
      )}

      {isAdmin && current.payment_status === 'PAID' && (
        <div className="payment-form">
          {!showRefundForm && (
            <button
              type="button"
              className="btn-secondary btn-outline-danger btn btn-sm"
              onClick={() => {
                setRefundAmount(current.payment_amount != null ? String(current.payment_amount) : '')
                setShowRefundForm(true)
              }}
            >
              Refund
            </button>
          )}
          {showRefundForm && (
            <form className="inline-form wrap" onSubmit={submitRefund}>
              {refundError && <p className="error">{refundError}</p>}
              <label className="inline-label">
                Amount
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  max={current.payment_amount ?? undefined}
                  value={refundAmount}
                  onChange={(e) => setRefundAmount(e.target.value)}
                  required
                />
              </label>
              <label className="inline-label" style={{ width: '100%' }}>
                Reason for refund
                <textarea
                  value={refundReason}
                  onChange={(e) => setRefundReason(e.target.value)}
                  placeholder="e.g. Patient double-charged at front desk"
                  required
                  rows={2}
                />
              </label>
              <button type="submit" className="btn btn-sm" disabled={refundBusy || !refundReason.trim()}>
                {refundBusy ? 'Saving…' : 'Confirm Refund'}
              </button>
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setShowRefundForm(false)}>
                Cancel
              </button>
            </form>
          )}
        </div>
      )}
    </div>
  )
}

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
  isAdmin,
  onPaymentUpdated,
}: {
  appointment: AdminAppointment
  onClose: () => void
  handlers: AppointmentActionHandlers
  busy: boolean
  isAdmin: boolean
  // Refreshes the parent's own list so the table/mobile card reflect a
  // payment/waiver recorded from inside this modal, without needing to
  // close and reopen it.
  onPaymentUpdated: () => void
}) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  // "View details" is meaningless from inside the details view it
  // opens; "Collect Payment"/"Waive Charge" are redundant with
  // PaymentSection's own real form above -- all three filtered out
  // here rather than taught to buildAppointmentActions, which still
  // needs to offer the first two everywhere else (the table row, the
  // mobile card).
  const rawActions = buildAppointmentActions(appointment, handlers, isAdmin)
  const REDUNDANT_IN_MODAL = new Set(['collect-payment', 'waive-charge'])
  const actions = {
    ...rawActions,
    primary: rawActions.primary && REDUNDANT_IN_MODAL.has(rawActions.primary.key) ? null : rawActions.primary,
    secondary: rawActions.secondary && REDUNDANT_IN_MODAL.has(rawActions.secondary.key) ? null : rawActions.secondary,
    overflow: rawActions.overflow.filter((item) => item.key !== 'details'),
  }

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
          {(() => {
            const arrival = describeArrival(appointment)
            return arrival ? (
              <span className={arrival.className}>
                {arrival.label}
                {arrival.sub && <span className="muted"> · {arrival.sub}</span>}
              </span>
            ) : (
              <span className={`pill status-${appointment.status.toLowerCase()}`}>
                {appointment.status.replace(/_/g, ' ')}
              </span>
            )
          })()}
          {appointment.token_number !== null && <span className="pill token-pill">Token #{appointment.token_number}</span>}
        </div>

        <p className="muted appointment-details-booked-on">Booked on {formatDateTime(appointment.created_at)}</p>

        <PaymentSection appointment={appointment} isAdmin={isAdmin} onUpdated={onPaymentUpdated} />

        <div className="appointment-details-actions">
          <AppointmentActionButtons actions={actions} busy={busy} />
        </div>
      </div>
    </div>,
    document.body,
  )
}
