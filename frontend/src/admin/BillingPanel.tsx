import { useEffect, useState } from 'react'
import { ArrowCounterClockwise, CreditCard, CurrencyInr, HandCoins, Receipt } from '@phosphor-icons/react'
import { ApiError, getBillingReport } from '../api'
import type { BillingReport } from '../types'
import { formatDate, formatDateTime } from '../format'

const WINDOW_OPTIONS = [7, 14, 30, 90] as const

// OPD billing reconciliation -- GET /dashboard/billing, built alongside
// refunds and itemized invoicing (migrations/0025/0026) but never
// wired to a page until now. Four independent sections, matching that
// endpoint's own four independent queries (see its docstring): money
// actually collected in the window, what's currently owed (not
// windowed -- "outstanding" means right now), and a waiver/refund
// audit trail for the window.
export default function BillingPanel() {
  const [days, setDays] = useState<(typeof WINDOW_OPTIONS)[number]>(14)
  const [report, setReport] = useState<BillingReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getBillingReport(days)
      .then((result) => {
        if (cancelled) return
        setReport(result)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load the billing report')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [days])

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Billing</h2>
          <p className="muted">Collections, outstanding balances, waivers, and refunds.</p>
        </div>
      </div>

      <div className="date-scope-row">
        {WINDOW_OPTIONS.map((option) => (
          <button
            key={option}
            type="button"
            className={days === option ? 'date-scope-pill active' : 'date-scope-pill'}
            onClick={() => setDays(option)}
          >
            Last {option} days
          </button>
        ))}
      </div>

      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading billing report…
        </div>
      )}

      {!loading && report && (
        <>
          <div className="dashboard-grid">
            <div className="stat-card">
              <span className="stat-icon tone-success" aria-hidden="true">
                <CurrencyInr size={22} weight="regular" />
              </span>
              <div className="stat-body">
                <span className="stat-value">₹{report.total_collected}</span>
                <span className="stat-label">Collected (last {report.window_days} days)</span>
              </div>
            </div>
            <div className="stat-card">
              <span className="stat-icon tone-warning" aria-hidden="true">
                <CreditCard size={22} weight="regular" />
              </span>
              <div className="stat-body">
                <span className="stat-value">{report.outstanding_unpaid.length}</span>
                <span className="stat-label">Outstanding Unpaid</span>
              </div>
            </div>
            <div className="stat-card">
              <span className="stat-icon tone-info" aria-hidden="true">
                <HandCoins size={22} weight="regular" />
              </span>
              <div className="stat-body">
                <span className="stat-value">{report.waivers.count}</span>
                <span className="stat-label">Waivers (last {report.window_days} days)</span>
              </div>
            </div>
            <div className="stat-card">
              <span className="stat-icon tone-danger" aria-hidden="true">
                <ArrowCounterClockwise size={22} weight="regular" />
              </span>
              <div className="stat-body">
                <span className="stat-value">₹{report.refunds.total_refunded}</span>
                <span className="stat-label">Refunded (last {report.window_days} days)</span>
              </div>
            </div>
          </div>

          <h3 className="dashboard-section-heading">Collections by payment method</h3>
          {report.collections_by_method.length === 0 ? (
            <p className="muted">No payments recorded in this window.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Method</th>
                  <th>Payments</th>
                  <th>Amount</th>
                </tr>
              </thead>
              <tbody>
                {report.collections_by_method.map((row) => (
                  <tr key={row.method ?? 'unknown'}>
                    <td>{row.method ?? <span className="muted">—</span>}</td>
                    <td>{row.count}</td>
                    <td>₹{row.amount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3 className="dashboard-section-heading">Collections by doctor</h3>
          {report.collections_by_doctor.length === 0 ? (
            <p className="muted">No payments recorded in this window.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Doctor</th>
                  <th>Payments</th>
                  <th>Amount</th>
                </tr>
              </thead>
              <tbody>
                {report.collections_by_doctor.map((row) => (
                  <tr key={row.doctor_id}>
                    <td>{row.doctor_name}</td>
                    <td>{row.count}</td>
                    <td>₹{row.amount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3 className="dashboard-section-heading">Outstanding unpaid</h3>
          {report.outstanding_unpaid.length === 0 ? (
            <p className="muted">Nothing outstanding right now.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Patient</th>
                  <th>Doctor</th>
                  <th>Status</th>
                  <th>Checked in</th>
                </tr>
              </thead>
              <tbody>
                {report.outstanding_unpaid.map((row) => (
                  <tr key={row.appointment_id}>
                    <td>{row.patient_name}</td>
                    <td>{row.doctor_name}</td>
                    <td>
                      <span className={`pill payment-${row.payment_status.toLowerCase()}`}>{row.payment_status}</span>
                    </td>
                    <td className="muted">{row.visited_at ? formatDateTime(row.visited_at) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3 className="dashboard-section-heading">Waivers</h3>
          {report.waivers.records.length === 0 ? (
            <p className="muted">No waivers in this window.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Patient</th>
                  <th>Doctor</th>
                  <th>Reason</th>
                  <th>Waived</th>
                </tr>
              </thead>
              <tbody>
                {report.waivers.records.map((row) => (
                  <tr key={row.appointment_id}>
                    <td>{row.patient_name}</td>
                    <td>{row.doctor_name}</td>
                    <td className="muted">{row.reason ?? '—'}</td>
                    <td className="muted">{row.waived_at ? formatDate(row.waived_at) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3 className="dashboard-section-heading">
            <Receipt size={16} weight="bold" aria-hidden="true" /> Refunds
          </h3>
          {report.refunds.records.length === 0 ? (
            <p className="muted">No refunds in this window.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Patient</th>
                  <th>Doctor</th>
                  <th>Amount</th>
                  <th>Reason</th>
                  <th>Refunded</th>
                </tr>
              </thead>
              <tbody>
                {report.refunds.records.map((row) => (
                  <tr key={row.appointment_id}>
                    <td>{row.patient_name}</td>
                    <td>{row.doctor_name}</td>
                    <td>₹{row.refund_amount}</td>
                    <td className="muted">{row.refund_reason ?? '—'}</td>
                    <td className="muted">{row.refunded_at ? formatDate(row.refunded_at) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}
