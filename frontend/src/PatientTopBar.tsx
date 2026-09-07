import { CloudSun, Moon, Sun } from '@phosphor-icons/react'
import LiveClock from './LiveClock'

// The in-card greeting header for SchedulingFlow.tsx and MyAppointments.tsx
// -- just the greeting and the clock now. Account navigation (My
// appointments / Schedule an appointment / Log out) moved up to the global
// AppHeader, since it's the same action regardless of which of these
// two screens is showing, rather than being duplicated in both.
export default function PatientTopBar({
  patientName,
  subtitle,
}: {
  patientName: string
  subtitle?: string
}) {
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const GreetingIcon = hour < 12 ? Sun : hour < 17 ? CloudSun : Moon

  return (
    <div className="topbar">
      <div className="topbar-greeting">
        <span className="topbar-greeting-icon" aria-hidden="true">
          <GreetingIcon size={22} weight="duotone" />
        </span>
        <div className="topbar-greeting-text">
          <span className="topbar-greeting-line">{greeting},</span>
          <span className="topbar-greeting-name">{patientName}</span>
          {subtitle && <span className="muted topbar-greeting-sub">{subtitle}</span>}
        </div>
      </div>
      <LiveClock />
    </div>
  )
}
