import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import { ApiError, getPaymentReceipt, sendPaymentReceipt } from '../api'
import type { PaymentReceipt } from '../types'
import { formatDateTime } from '../format'

// OPD/HIMS master spec Phase 13, section 42 -- a printable receipt for
// one payment. "Print" and "Download" are deliberately the same
// action: the browser's own print dialog already offers "Save as PDF",
// so a second, server-rendered PDF path would just be a redundant
// implementation of what window.print() already gives for free (same
// stance Phase 9 took on the bill itself being screen-only). "Send to
// patient" reuses the existing mock-notification infrastructure
// (app/services/notifications.py's KIND_RECEIPT) -- same staff-
// initiated exception as check-in/queue-token notifications.
//
// .receipt-print-area is the only thing @media print (styles.css)
// leaves visible -- everything else on the page (sidebar, top bar, the
// rest of the modal chrome) is hidden for the print, so what comes out
// of the printer/PDF is just the receipt itself, not a screenshot of
// the whole app.
export default function PaymentReceiptModal({
  appointmentId,
  paymentId,
  onClose,
}: {
  appointmentId: number
  paymentId: number
  onClose: () => void
}) {
  const [receipt, setReceipt] = useState<PaymentReceipt | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getPaymentReceipt(appointmentId, paymentId)
      .then((result) => {
        if (!cancelled) setReceipt(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load this receipt')
      })
    return () => {
      cancelled = true
    }
  }, [appointmentId, paymentId])

  async function handleSend() {
    setSending(true)
    setSendResult(null)
    try {
      await sendPaymentReceipt(appointmentId, paymentId)
      setSendResult('Sent to patient.')
    } catch (err) {
      setSendResult(err instanceof ApiError ? err.message : 'Could not send this receipt')
    } finally {
      setSending(false)
    }
  }

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel receipt-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Payment receipt"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        {error && <p className="error">{error}</p>}

        {!error && receipt === null && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading receipt…
          </div>
        )}

        {receipt && (
          <>
            <div className="receipt-print-area">
              <h3 className="appointment-details-heading">{receipt.hospital_name}</h3>
              <p className="muted" style={{ marginTop: 0 }}>
                Receipt {receipt.receipt_number} · Bill {receipt.invoice_number}
              </p>

              <div className="receipt-meta-grid">
                <span>Patient</span>
                <span>
                  {receipt.patient_name} ({receipt.patient_uhid})
                </span>
                <span>Date/time</span>
                <span>{formatDateTime(receipt.recorded_at)}</span>
                <span>Cashier</span>
                <span>{receipt.cashier}</span>
              </div>

              <table className="data-table">
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {receipt.services.map((s, i) => (
                    <tr key={i}>
                      <td>{s.description}</td>
                      <td>₹{s.amount.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <table className="data-table">
                <tbody>
                  <tr>
                    <td>Gross</td>
                    <td>₹{receipt.gross_amount.toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td>Discount</td>
                    <td>−₹{receipt.discount_amount.toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td>Tax</td>
                    <td>₹{receipt.tax_amount.toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td>
                      <strong>Net</strong>
                    </td>
                    <td>
                      <strong>₹{receipt.net_amount.toFixed(2)}</strong>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <strong>Paid this transaction</strong>
                    </td>
                    <td>
                      <strong>₹{receipt.payment_amount.toFixed(2)}</strong>
                    </td>
                  </tr>
                  <tr>
                    <td>Method</td>
                    <td>
                      {receipt.payment_method}
                      {receipt.transaction_id ? ` · ${receipt.transaction_id}` : ''}
                    </td>
                  </tr>
                  <tr>
                    <td>Status</td>
                    <td>
                      <span className={`pill status-${receipt.payment_status.toLowerCase()}`}>
                        {receipt.payment_status}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="doctor-quick-actions receipt-actions">
              <button type="button" className="btn btn-sm" onClick={() => window.print()}>
                Print / Download
              </button>
              <button type="button" className="btn-secondary btn btn-sm" disabled={sending} onClick={handleSend}>
                {sending ? 'Sending…' : 'Send to patient'}
              </button>
            </div>
            {sendResult && <p className="muted">{sendResult}</p>}
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
