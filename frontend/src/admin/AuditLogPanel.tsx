import { useEffect, useState } from 'react'
import { ApiError, getAuditLog, listStaffAccounts } from '../api'
import type { AuditLogEntry, StaffAccount } from '../types'
import { formatDateTime } from '../format'

// master spec audit "subsequent gaps" list: "No frontend page to view
// the audit log. The backend GET /api/audit-log endpoint exists
// (filterable, capped at 200 rows) but nothing in the sidebar or admin
// app links to it." This is that page -- a thin filter form over the
// existing endpoint, no new backend work. Capped at 200 rows
// server-side (see app/api/audit_log.py's own MAX_RESULTS comment);
// this page surfaces that as "narrow your filters" rather than
// pretending there's pagination past it.
export default function AuditLogPanel() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([])
  const [staffAccounts, setStaffAccounts] = useState<StaffAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [staffId, setStaffId] = useState('')
  const [action, setAction] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [resourceId, setResourceId] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  useEffect(() => {
    listStaffAccounts()
      .then(setStaffAccounts)
      .catch(() => undefined)
  }, [])

  function load() {
    setLoading(true)
    setError(null)
    getAuditLog({
      staff_id: staffId ? Number(staffId) : undefined,
      action: action.trim() || undefined,
      resource_type: resourceType.trim() || undefined,
      resource_id: resourceId ? Number(resourceId) : undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    })
      .then(setEntries)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the audit log'))
      .finally(() => setLoading(false))
  }

  useEffect(load, []) // eslint-disable-line react-hooks/exhaustive-deps

  function handleFilter(e: React.FormEvent) {
    e.preventDefault()
    load()
  }

  function resetFilters() {
    setStaffId('')
    setAction('')
    setResourceType('')
    setResourceId('')
    setDateFrom('')
    setDateTo('')
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Audit Log</h2>
          <p className="muted">Who did what, across every admin action -- newest first, capped at 200 rows.</p>
        </div>
      </div>

      <form className="inline-form wrap" onSubmit={handleFilter}>
        <select value={staffId} onChange={(e) => setStaffId(e.target.value)}>
          <option value="">All staff</option>
          {staffAccounts.map((s) => (
            <option key={s.id} value={s.id}>
              {s.username}
            </option>
          ))}
        </select>
        <input placeholder="Action (e.g. staff.create)" value={action} onChange={(e) => setAction(e.target.value)} />
        <input
          placeholder="Resource type (e.g. doctor)"
          value={resourceType}
          onChange={(e) => setResourceType(e.target.value)}
        />
        <input
          placeholder="Resource ID"
          type="number"
          value={resourceId}
          onChange={(e) => setResourceId(e.target.value)}
        />
        <label className="inline-label">
          From
          <input type="datetime-local" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </label>
        <label className="inline-label">
          To
          <input type="datetime-local" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </label>
        <button type="submit" className="btn btn-sm" disabled={loading}>
          {loading ? 'Loading…' : 'Filter'}
        </button>
        <button type="button" className="btn-secondary btn btn-sm" onClick={resetFilters}>
          Reset
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading audit log…
        </div>
      )}

      {!loading && entries.length === 0 && !error && (
        <div className="state-block empty">No audit log entries match these filters.</div>
      )}

      {!loading && entries.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Staff</th>
              <th>Action</th>
              <th>Resource</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td>{formatDateTime(entry.created_at)}</td>
                <td>{entry.staff_username ?? <span className="muted">—</span>}</td>
                <td>{entry.action}</td>
                <td>
                  {entry.resource_type}
                  {entry.resource_id !== null ? ` #${entry.resource_id}` : ''}
                </td>
                <td className="muted">{entry.details ? JSON.stringify(entry.details) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!loading && entries.length === 200 && (
        <p className="muted">Showing the most recent 200 matches -- narrow your filters to see further back.</p>
      )}
    </section>
  )
}
