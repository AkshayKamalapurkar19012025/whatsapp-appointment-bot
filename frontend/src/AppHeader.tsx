import { CalendarCheck, Cross, SignOut } from '@phosphor-icons/react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './components/ui/dropdown-menu'

// The one persistent brand bar across the whole patient-facing app --
// shown above the login card, the scheduling flow, and My Appointments
// alike. Only the account actions on the right are conditional on
// being logged in; the brand on the left is always there. Account
// navigation (switching between "scheduling" and "appointments") stays a
// plain, always-labelled button; logging out lives inside a small
// account menu (same DropdownMenu primitive as AdminTopBar.tsx's
// account menu) rather than a second competing text link, so the one
// primary action keeps the room it needs at narrow widths instead of
// degrading to an unlabelled icon.
export default function AppHeader({
  loggedIn,
  patientName,
  primaryLabel,
  primaryLabelShort,
  onPrimaryAction,
  onLogout,
}: {
  loggedIn: boolean
  patientName?: string
  primaryLabel?: string
  primaryLabelShort?: string
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
          <button type="button" className="app-header-link" onClick={onPrimaryAction}>
            <CalendarCheck size={18} weight="bold" aria-hidden="true" />
            <span className="app-header-link-label-full">{primaryLabel}</span>
            <span className="app-header-link-label-short">{primaryLabelShort}</span>
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger
              className="app-header-account-trigger"
              aria-label={patientName ? `Account menu for ${patientName}` : 'Account menu'}
            >
              {patientName ? patientName.charAt(0).toUpperCase() : ''}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {patientName && <div className="app-header-account-label">Signed in as {patientName}</div>}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="danger" onSelect={onLogout}>
                <SignOut size={16} weight="regular" />
                Log out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </nav>
      )}
    </header>
  )
}
