import { useEffect, useState } from 'react'
import { CaretLeft, CaretRight } from '@phosphor-icons/react'
import { ApiError, getInvoiceHistory } from '../api'
import type { InvoiceHistoryEntry } from '../types'
import { formatDateTime } from '../format'

const PAGE_SIZE = 20

// master spec audit "subsequent gaps" list (screen 29): "Per-visit
// billing exists; no dedicated cross-visit billing-history screen."
// Every invoice across every patient/visit, newest first, filterable
// by patient name and date range -- server-paginated (same discipline
// as PatientsPanel.tsx's own gap fix), not a client-side filter over
// everything.
export default function BillingHistoryPanel() {
  const [items, setItems] = useState<InvoiceHistoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [patientName, setPatientName] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setPage(0)
  }, [patientName, dateFrom, dateTo])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getInvoiceHistory({
      patient_name: patientName.trim() || undefined,
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
        setError(err instanceof ApiError ? err.message : 'Could not load billing history')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [patientName, dateFrom, dateTo, page])

  const pageStart = total === 0 ? 0 : page * PAGE_SIZE + 1
  const pageEnd = Math.min(total, (page + 1) * PAGE_SIZE)

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Billing History</h2>
          <p className="muted">Every invoice across every visit, newest first.</p>
        </div>
      </div>

      <form className="inline-form wrap" onSubmit={(e) => e.preventDefault()}>
        <input
          placeholder="Search by patient name"
          value={patientName}
          onChange={(e) => setPatientName(e.target.value)}
        />
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
        <div className="state-block empty">No invoices match these filters.</div>
      )}

      {!loading && items.length > 0 && (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>Invoice</th>
                <th>Patient</th>
                <th>Doctor</th>
                <th>Date</th>
                <th>Net</th>
                <th>Paid</th>
                <th>Balance</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id}>
                  <td>{i.invoice_number}</td>
                  <td>
                    {i.patient_name} <span className="muted">({i.patient_uhid})</span>
                  </td>
                  <td>{i.doctor_name}</td>
                  <td>{formatDateTime(i.created_at)}</td>
                  <td>₹{i.net_amount.toFixed(2)}</td>
                  <td>₹{i.paid_amount.toFixed(2)}</td>
                  <td>₹{i.balance.toFixed(2)}</td>
                  <td>
                    <span className={`pill status-${i.payment_status.toLowerCase()}`}>
                      {i.payment_status.replace('_', ' ')}
                    </span>
                    {i.status === 'VOID' && <span className="pill status-cancelled">VOID</span>}
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
