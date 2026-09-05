import { useEffect, useState } from 'react'
import {
  CalendarBlank,
  CalendarCheck,
  CalendarPlus,
  Clock,
  Stethoscope,
  UsersThree,
  XCircle,
} from '@phosphor-icons/react'
import { ApiError, getDashboardStats } from '../api'
import type { DashboardStats } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'

interface StatCardDef {
  key: keyof DashboardStats
  label: string
  icon: React.ReactElement
}

// Limited to what this app's schema can actually answer: appointments
// only ever carry status BOOKED or CANCELLED (no Pending/Confirmed/
// Completed/Rejected/Visited lifecycle), so there's no richer status
// breakdown to show beyond these six counts.
const STAT_CARDS: StatCardDef[] = [
  { key: 'today_appointments', label: "Today's Appointments", icon: <CalendarCheck size={22} weight="regular" /> },
  { key: 'upcoming_appointments', label: 'Upcoming Appointments', icon: <Clock size={22} weight="regular" /> },
  { key: 'total_appointments', label: 'Total Appointments', icon: <CalendarBlank size={22} weight="regular" /> },
  { key: 'cancelled_appointments', label: 'Cancelled Appointments', icon: <XCircle size={22} weight="regular" /> },
  { key: 'total_doctors', label: 'Total Doctors', icon: <Stethoscope size={22} weight="regular" /> },
  { key: 'total_patients', label: 'Total Patients', icon: <UsersThree size={22} weight="regular" /> },
]

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
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const statsGridRef = useStaggerReveal<HTMLDivElement>([stats])

  useEffect(() => {
    getDashboardStats()
      .then(setStats)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load dashboard stats'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <section>
      <h2>Welcome back, {staffName}</h2>
      <p className="muted">Here&apos;s what&apos;s happening across the clinic today.</p>
      {error && <p className="error">{error}</p>}

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

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading dashboard…
        </div>
      )}

      {!loading && stats && (
        <div className="stat-grid" ref={statsGridRef}>
          {STAT_CARDS.map((card) => (
            <div className="stat-card" key={card.key}>
              <span className="stat-icon" aria-hidden="true">
                {card.icon}
              </span>
              <div className="stat-body">
                <span className="stat-value">{stats[card.key]}</span>
                <span className="stat-label">{card.label}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
