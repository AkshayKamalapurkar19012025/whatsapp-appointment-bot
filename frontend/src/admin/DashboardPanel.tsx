import { useEffect, useState } from 'react'
import {
  Bell,
  CalendarBlank,
  CalendarCheck,
  CalendarPlus,
  CheckCircle,
  CheckCircle as CheckCircleClear,
  Clock,
  HourglassMedium,
  Stethoscope,
  UserCheck,
  UsersThree,
  WarningCircle,
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
import { ApiError, getActiveExceptions, getDashboardStats, getDashboardTrends } from '../api'
import type { DashboardStats, DashboardTrends, ExceptionType, OperationalException } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'

// Which of the .stat-icon tone-* colors each exception family gets --
// billing/payment gaps are the most consequential (money not
// collected), so they read as danger; queue-stage waits are warning
// (time-sensitive but routine); everything else is informational.
const EXCEPTION_TONE: Record<ExceptionType, 'warning' | 'danger' | 'info'> = {
  WAITING_FOR_TRIAGE: 'warning',
  WAITING_FOR_DOCTOR: 'warning',
  ORDER_PENDING: 'info',
  PRESCRIPTION_NOT_DISPENSED: 'info',
  BILLING_NOT_STARTED: 'danger',
  PAYMENT_PENDING: 'danger',
}

function formatAge(minutes: number): string {
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`
}

function ExceptionRow({ exception, onGoToPatients }: { exception: OperationalException; onGoToPatients: () => void }) {
  return (
    <li className="exception-row">
      <span className={`stat-icon tone-${EXCEPTION_TONE[exception.type]}`} aria-hidden="true">
        <WarningCircle size={18} weight="regular" />
      </span>
      <div className="exception-row-body">
        <p>{exception.what_happened}</p>
        <p className="muted">{exception.recommended_action}</p>
      </div>
      <div className="exception-row-side">
        <span className="pill status-pending">{formatAge(exception.age_minutes)} ago</span>
        <span className="muted">{exception.who_should_act}</span>
        <button type="button" className="link" onClick={onGoToPatients}>
          View Patient
        </button>
      </div>
    </li>
  )
}

interface StatCardDef {
  key: keyof DashboardStats
  label: string
  icon: React.ReactElement
  // Grid width in the 4-column dashboard-grid -- default 1.
  span?: 1 | 2
  // Semantic tone for the icon badge (.stat-icon.tone-*, styles.css) --
  // omitted means the plain default primary teal, for neutral volume
  // counts that aren't themselves a good/bad signal (today's/upcoming
  // appointments). A status this genuinely represents good/bad news
  // always gets a matching tone, so e.g. Cancelled/Rejected don't sit
  // in the same teal badge as Confirmed/Completed.
  tone?: 'success' | 'warning' | 'danger' | 'info'
}

const OVERVIEW_CARDS: StatCardDef[] = [
  { key: 'today_appointments', label: "Today's Appointments", icon: <CalendarCheck size={22} weight="regular" /> },
  { key: 'upcoming_appointments', label: 'Upcoming Appointments', icon: <Clock size={22} weight="regular" /> },
]

const REVIEW_CARDS: StatCardDef[] = [
  {
    key: 'pending_appointments',
    label: 'Pending Appointments',
    icon: <HourglassMedium size={22} weight="regular" />,
    span: 2,
    tone: 'warning',
  },
  {
    key: 'confirmed_appointments',
    label: 'Confirmed Appointments',
    icon: <UserCheck size={22} weight="regular" />,
    span: 2,
    tone: 'success',
  },
]

const STATUS_CARDS: StatCardDef[] = [
  { key: 'cancelled_appointments', label: 'Cancelled Appointments', icon: <XCircle size={22} weight="regular" />, tone: 'danger' },
  { key: 'rejected_appointments', label: 'Rejected Appointments', icon: <XCircle size={22} weight="regular" />, tone: 'danger' },
  { key: 'completed_appointments', label: 'Completed Appointments', icon: <CheckCircle size={22} weight="regular" />, tone: 'success' },
  { key: 'visited_appointments', label: 'Visited Appointments', icon: <CalendarBlank size={22} weight="regular" />, tone: 'success' },
]

function StatCard({ def, value }: { def: StatCardDef; value: number }) {
  return (
    <div className={`stat-card${def.span === 2 ? ' stat-card--wide' : ''}`}>
      <span className={`stat-icon${def.tone ? ` tone-${def.tone}` : ''}`} aria-hidden="true">
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
  const [exceptions, setExceptions] = useState<OperationalException[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const statsGridRef = useStaggerReveal<HTMLDivElement>([stats])

  useEffect(() => {
    Promise.all([getDashboardStats(), getDashboardTrends(), getActiveExceptions()])
      .then(([statsResult, trendsResult, exceptionsResult]) => {
        setStats(statsResult)
        setTrends(trendsResult)
        setExceptions(exceptionsResult.exceptions)
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

          <div className="exceptions-card">
            <h3>
              <Bell size={18} weight="regular" aria-hidden="true" /> Needs Attention
              {exceptions && exceptions.length > 0 ? ` (${exceptions.length})` : ''}
            </h3>
            {exceptions && exceptions.length === 0 && (
              <p className="muted exceptions-empty">
                <CheckCircleClear size={16} weight="regular" aria-hidden="true" /> Nothing overdue right now.
              </p>
            )}
            {exceptions && exceptions.length > 0 && (
              <ul className="exceptions-list">
                {exceptions.map((exception, index) => (
                  <ExceptionRow
                    key={`${exception.type}-${index}`}
                    exception={exception}
                    onGoToPatients={onGoToPatients}
                  />
                ))}
              </ul>
            )}
          </div>

          <h3 className="dashboard-section-heading">Appointment Status</h3>

          {STATUS_CARDS.map((def) => (
            <StatCard key={def.key} def={def} value={stats[def.key]} />
          ))}

          <h3 className="dashboard-section-heading">Analytics</h3>

          <div className="chart-card">
            <h3>Appointment Requests</h3>
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
                <Line type="monotone" dataKey="count" stroke="var(--color-primary)" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="chart-card">
            <h3>Patient Registrations</h3>
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
                <Line type="monotone" dataKey="count" stroke="var(--color-info)" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </section>
  )
}
