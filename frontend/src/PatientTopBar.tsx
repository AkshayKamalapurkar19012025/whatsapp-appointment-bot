import type { ReactNode } from 'react'
import LiveClock from './LiveClock'

// The identical header structure BookingFlow.tsx and MyAppointments.tsx
// each used to build inline (Hi, {name} / LiveClock / action links) --
// extracted so the greeting hierarchy and clock placement stay in sync
// across both screens instead of drifting. `subtitle` and `actions` are
// the only things that differ per screen (a short line under the
// greeting, and which links sit on the right).
export default function PatientTopBar({
  patientName,
  subtitle,
  actions,
}: {
  patientName: string
  subtitle?: string
  actions: ReactNode
}) {
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'

  return (
    <div className="topbar">
      <div className="topbar-greeting">
        <span className="topbar-greeting-line">
          {greeting}, <strong>{patientName}</strong>
        </span>
        {subtitle && <span className="muted topbar-greeting-sub">{subtitle}</span>}
      </div>
      <div className="topbar-right">
        <LiveClock />
        <div className="topbar-actions">{actions}</div>
      </div>
    </div>
  )
}
