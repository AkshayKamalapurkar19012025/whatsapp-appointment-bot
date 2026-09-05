import { useEffect, useState } from 'react'
import {
  CalendarBlank,
  CalendarCheck,
  CalendarPlus,
  CheckCircle,
  Clock,
  HourglassMedium,
  Stethoscope,
  UserCheck,
  UsersThree,
  XCircle,
} from '@phosphor-icons/react'
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts'
import { ApiError, getDashboardStats, getDashboardTrends } from '../api'
import type { DashboardStats, DashboardTrends } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'

interface StatCardDef {
  key: keyof DashboardStats
  label: string
  icon: React.ReactElement
  // Grid width in the 4-column dashboard-grid -- default 1.
  span?: 1 | 2
}

const OVERVIEW_CARDS: StatCardDef[] = [
  { key: 'today_appointments', label: "Today's Appointments", icon: <CalendarCheck size={22} weight="regular" /> },
  { key: 'upcoming_appointments', label: 'Upcoming Appointments', icon: <Clock size={22} weight="regular" /> },
]

const REVIEW_CARDS: StatCardDef[] = [
  { key: 'pending_appointments', label: 'Pending Appointments', icon: <HourglassMedium size={22} weight="regular" />, span: 2 },
  { key: 'confirmed_appointments', label: 'Confirmed Appointments', icon: <UserCheck size={22} weight="regular" />, span: 2 },
]

const STATUS_CARDS: StatCardDef[] = [
  { key: 'cancelled_appointments', label: 'Cancelled Appointments', icon: <XCircle size={22} weight="regular" /> },
  { key: 'rejected_appointments', label: 'Rejected Appointments', icon: <XCircle size={22} weight="regular" /> },
  { key: 'completed_appointments', label: 'Completed Appointments', icon: <CheckCircle size={22} weight="regular" /> },
  { key: 'visited_appointments', label: 'Visited Appointments', icon: <CalendarBlank size={22} weight="regular" /> },
]

function StatCard({ def, value }: { def: StatCardDef; value: number }) {
  return (
    <div className={`stat-card${def.span === 2 ? ' stat-card--wide' : ''}`}>
      <span className="stat-icon" aria-hidden="true">
        {def.icon}
      </span>
      <div className="stat-body">
        <span className="stat-value">{value}</span>
        <span className="stat-label">{def.label}</span>
      </div>
    </div>
  )
}

interface DashboardPanelProps {
  staffName: string
  onBookAppointment: () => void
  onGoToDoctors: () => void
  onGoToPatients: () => void
}

export default function DashboardPanel({
  staffName,
  onBookAppointment,
  onGoToDoctors,
  onGoToPatients,
}: DashboardPanelProps) {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [trends, setTrends] = useState<DashboardTrends | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const statsGridRef = useStaggerReveal<HTMLDivElement>([stats])

  useEffect(() => {
    Promise.all([getDashboardStats(), getDashboardTrends()])
      .then(([statsResult, trendsResult]) => {
        setStats(statsResult)
        setTrends(trendsResult)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load dashboard data'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <section>
      <h2>Dashboard</h2>
      <p className="muted">Here&apos;s what&apos;s happening across the clinic today.</p>
      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading dashboard…
        </div>
      )}

      {!loading && stats && (
        <div className="dashboard-grid" ref={statsGridRef}>
          <div className="welcome-card">
            <h3>Welcome back, {staffName}</h3>
            <p className="muted">A quick overview of the clinic, and shortcuts to get things done.</p>
            <div className="quick-actions">
              <button type="button" className="quick-action-btn" onClick={onBookAppointment}>
                <CalendarPlus size={20} weight="regular" />
                Add New Appointment
              </button>
              <button type="button" className="quick-action-btn" onClick={onGoToDoctors}>
                <Stethoscope size={20} weight="regular" />
                Add Doctor
              </button>
              <button type="button" className="quick-action-btn" onClick={onGoToPatients}>
                <UsersThree size={20} weight="regular" />
                Add Patient
              </button>
            </div>
            <div className="welcome-totals">
              <div>
                <span className="welcome-total-value">{stats.total_doctors}</span>
                <span className="welcome-total-label">Total Doctors</span>
              </div>
              <div>
                <span className="welcome-total-value">{stats.total_patients}</span>
                <span className="welcome-total-label">Total Patients</span>
              </div>
              <div>
                <span className="welcome-total-value">{stats.total_appointments}</span>
                <span className="welcome-total-label">Total Appointments</span>
              </div>
            </div>
          </div>

          {OVERVIEW_CARDS.map((def) => (
            <StatCard key={def.key} def={def} value={stats[def.key]} />
          ))}

          {REVIEW_CARDS.map((def) => (
            <StatCard key={def.key} def={def} value={stats[def.key]} />
          ))}

          <h3 className="dashboard-section-heading">Appointment Status</h3>

          {STATUS_CARDS.map((def) => (
            <StatCard key={def.key} def={def} value={stats[def.key]} />
          ))}

          <h3 className="dashboard-section-heading">Analytics</h3>

          <div className="chart-card">
            <h4>Appointment Requests</h4>
            <p className="muted">New appointment requests over the last {trends?.appointments.length ?? 0} days</p>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={trends?.appointments ?? []} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
                  tickFormatter={(value: string) => value.slice(5)}
                />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }} width={28} />
                <Tooltip
                  labelFormatter={(value) => String(value)}
                  contentStyle={{ borderRadius: 8, border: '1px solid var(--color-border)', fontSize: 13 }}
                />
                <Line type="monotone" dataKey="count" stroke="#1f7a63" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="chart-card">
            <h4>Patient Registrations</h4>
            <p className="muted">New patients registered over the last {trends?.patients.length ?? 0} days</p>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={trends?.patients ?? []} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
                  tickFormatter={(value: string) => value.slice(5)}
                />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }} width={28} />
                <Tooltip
                  labelFormatter={(value) => String(value)}
                  contentStyle={{ borderRadius: 8, border: '1px solid var(--color-border)', fontSize: 13 }}
                />
                <Line type="monotone" dataKey="count" stroke="#9bcddc" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </section>
  )
}
