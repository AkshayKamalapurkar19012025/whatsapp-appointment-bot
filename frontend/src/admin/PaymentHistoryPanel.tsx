import { useEffect, useState } from 'react'
import { CaretLeft, CaretRight } from '@phosphor-icons/react'
import { ApiError, getPaymentHistory } from '../api'
import type { BillPaymentMethod, PaymentHistoryEntry } from '../types'
import { formatDateTime } from '../format'

const PAGE_SIZE = 20

const METHOD_OPTIONS: BillPaymentMethod[] = ['CASH', 'UPI', 'CARD', 'BANK_TRANSFER', 'INSURANCE', 'OTHER']

// master spec audit "subsequent gaps" list (screen 30): "Per-visit
// payments list exists; no dedicated cross-visit payment-history
// screen." Every payment across every invoice, newest first,
// filterable by patient name, method, and date range --
// server-paginated, same as BillingHistoryPanel.tsx.
export default function PaymentHistoryPanel() {
  const [items, setItems] = useState<PaymentHistoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [patientName, setPatientName] = useState('')
  const [method, setMethod] = useState<BillPaymentMethod | ''>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setPage(0)
  }, [patientName, method, dateFrom, dateTo])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getPaymentHistory({
      patient_name: patientName.trim() || undefined,
      method: method || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((result) => {
        if (cancelled) return
        setItems(result.items)
        setTotal(result.total)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load payment history')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [patientName, method, dateFrom, dateTo, page])

  const pageStart = total === 0 ? 0 : page * PAGE_SIZE + 1
  const pageEnd = Math.min(total, (page + 1) * PAGE_SIZE)

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Payment History</h2>
          <p className="muted">Every payment across every invoice, newest first.</p>
        </div>
      </div>

      <form className="inline-form wrap" onSubmit={(e) => e.preventDefault()}>
        <input
          placeholder="Search by patient name"
          value={patientName}
          onChange={(e) => setPatientName(e.target.value)}
        />
        <select value={method} onChange={(e) => setMethod(e.target.value as BillPaymentMethod | '')}>
          <option value="">All methods</option>
          {METHOD_OPTIONS.map((m) => (
            <option key={m} value={m}>
              {m.replace('_', ' ')}
            </option>
          ))}
        </select>
        <label className="inline-label">
          From
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </label>
        <label className="inline-label">
          To
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </label>
      </form>

      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}

      {!loading && items.length === 0 && !error && (
        <div className="state-block empty">No payments match these filters.</div>
      )}

      {!loading && items.length > 0 && (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>Receipt</th>
                <th>Patient</th>
                <th>Invoice</th>
                <th>Date</th>
                <th>Amount</th>
                <th>Method</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p) => (
                <tr key={p.id}>
                  <td>{p.receipt_number}</td>
                  <td>
                    {p.patient_name} <span className="muted">({p.patient_uhid})</span>
                  </td>
                  <td>{p.invoice_number}</td>
                  <td>{formatDateTime(p.recorded_at)}</td>
                  <td>
                    ₹{p.amount.toFixed(2)}
                    {p.refunded_amount > 0 && (
                      <div className="muted">−₹{p.refunded_amount.toFixed(2)} refunded</div>
                    )}
                  </td>
                  <td>{p.method.replace('_', ' ')}</td>
                  <td>
                    <span className={`pill status-${p.status.toLowerCase()}`}>{p.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="admin-pagination">
            <span className="muted">
              {pageStart}–{pageEnd} of {total}
            </span>
            <div className="admin-pagination-actions">
              <button
                type="button"
                className="btn-secondary btn btn-sm"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                aria-label="Previous page"
              >
                <CaretLeft size={14} weight="bold" />
              </button>
              <button
                type="button"
                className="btn-secondary btn btn-sm"
                disabled={pageEnd >= total}
                onClick={() => setPage((p) => p + 1)}
                aria-label="Next page"
              >
                <CaretRight size={14} weight="bold" />
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
