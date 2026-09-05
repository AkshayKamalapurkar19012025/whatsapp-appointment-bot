import { CalendarCheck, Cross, SignOut } from '@phosphor-icons/react'

// The one persistent brand bar across the whole patient-facing app --
// shown above the login card, the booking flow, and My Appointments
// alike. Only the account actions on the right are conditional on
// being logged in; the brand on the left is always there. Account
// navigation (switching between "booking" and "appointments", logging
// out) lives here now rather than duplicated inside BookingFlow.tsx and
// MyAppointments.tsx, since it's the same action regardless of which
// screen is currently showing.
export default function AppHeader({
  loggedIn,
  primaryLabel,
  onPrimaryAction,
  onLogout,
}: {
  loggedIn: boolean
  primaryLabel?: string
  onPrimaryAction?: () => void
  onLogout?: () => void
}) {
  return (
    <header className="app-header">
      <div className="app-header-brand">
        <span className="app-header-logo" aria-hidden="true">
          <Cross size={18} weight="bold" />
        </span>
        <span className="app-header-name">Aditi Hospital</span>
      </div>

      {loggedIn && (
        <nav className="app-header-actions">
          <button type="button" className="app-header-link" onClick={onPrimaryAction} aria-label={primaryLabel}>
            <CalendarCheck size={16} weight="bold" aria-hidden="true" />
            <span className="app-header-link-label">{primaryLabel}</span>
          </button>
          <span className="app-header-divider" aria-hidden="true" />
          <button
            type="button"
            className="app-header-link app-header-link-muted"
            onClick={onLogout}
            aria-label="Log out"
          >
            <SignOut size={16} weight="bold" aria-hidden="true" />
            <span className="app-header-link-label">Log out</span>
          </button>
        </nav>
      )}
    </header>
  )
}
