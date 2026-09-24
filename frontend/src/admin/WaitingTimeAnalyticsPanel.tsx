import { useEffect, useState } from 'react'
import { HourglassMedium, UsersThree } from '@phosphor-icons/react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ApiError, getWaitingTimeAnalytics, listAllDoctors } from '../api'
import type { Doctor, WaitingTimeAnalytics } from '../types'

const DAYS_OPTIONS = [7, 14, 30, 90]

// master spec audit "subsequent gaps" list (screen 33): "Not built as
// its own screen (raw average shown inline on Appointments page)."
// AppointmentsPanel.tsx's avgWaitMinutes is a live snapshot -- only
// today's currently-waiting patients. This is the historical view:
// actual wait time for patients already seen, trended over a real
// date range and broken down by doctor.
export default function WaitingTimeAnalyticsPanel() {
  const [days, setDays] = useState(14)
  const [doctorId, setDoctorId] = useState<number | ''>('')
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [data, setData] = useState<WaitingTimeAnalytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listAllDoctors()
      .then(setDoctors)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getWaitingTimeAnalytics(days, doctorId === '' ? undefined : doctorId)
      .then((result) => {
        if (cancelled) return
        setData(result)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load waiting-time analytics')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [days, doctorId])

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Waiting-Time Analytics</h2>
          <p className="muted">
            How long checked-in patients actually waited before their consultation started, trended over time.
          </p>
        </div>
      </div>

      <form className="inline-form wrap" onSubmit={(e) => e.preventDefault()}>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {DAYS_OPTIONS.map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </select>
        <select value={doctorId} onChange={(e) => setDoctorId(e.target.value === '' ? '' : Number(e.target.value))}>
          <option value="">All doctors</option>
          {doctors.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </form>

      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}

      {!loading && data && (
        <>
          <div className="dashboard-grid">
            <div className="stat-card">
              <span className="stat-icon tone-warning" aria-hidden="true">
                <HourglassMedium size={20} weight="bold" />
              </span>
              <div className="stat-body">
                <span className="stat-value">
                  {data.overall.count > 0 ? `${data.overall.avg_wait_minutes} mins` : '—'}
                </span>
                <span className="stat-label">Average wait · last {data.window_days} days</span>
              </div>
            </div>
            <div className="stat-card">
              <span className="stat-icon" aria-hidden="true">
                <UsersThree size={20} weight="bold" />
              </span>
              <div className="stat-body">
                <span className="stat-value">{data.overall.count}</span>
                <span className="stat-label">Patients seen with a recorded wait</span>
              </div>
            </div>
          </div>

          <div className="chart-card">
            <h4>Average Wait Time By Day</h4>
            <p className="muted">Minutes from check-in to consultation start, last {data.window_days} days.</p>
            {data.overall.count === 0 ? (
              <div className="state-block empty">No completed waits in this window yet.</div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={data.by_day} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
                    tickFormatter={(value: string) => value.slice(5)}
                  />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }} width={28} />
                  <Tooltip
                    labelFormatter={(value) => String(value)}
                    formatter={(value) => [`${value} mins`, 'Avg. wait']}
                    contentStyle={{ borderRadius: 8, border: '1px solid var(--color-border)', fontSize: 13 }}
                  />
                  <Bar dataKey="avg_wait_minutes" fill="var(--color-primary)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="chart-card">
            <h4>By Doctor</h4>
            {data.by_doctor.length === 0 ? (
              <div className="state-block empty">No completed waits in this window yet.</div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Doctor</th>
                    <th>Patients seen</th>
                    <th>Average wait</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_doctor.map((row) => (
                    <tr key={row.doctor_id}>
                      <td>{row.doctor_name}</td>
                      <td>{row.count}</td>
                      <td>{row.avg_wait_minutes} mins</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </section>
  )
}
