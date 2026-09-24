import { CaretDown, Globe, SignOut } from '@phosphor-icons/react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../components/ui/dropdown-menu'
import GlobalSearchBar from './GlobalSearchBar'
import NotificationBell from './NotificationBell'

// Top-right account menu: the one place a logged-in staff member's
// identity, the link out to the patient-facing site, and their Logout
// action all live -- replacing both the old plain-text Logout entry
// that used to sit at the bottom of the left sidebar's nav list and the
// separate "Patient site" link in the sidebar footer (per explicit
// feedback -- neither is really a page in this app's own nav, they're
// account-menu-shaped actions, so both belong together here instead of
// split across the sidebar).
export default function AdminTopBar({
  username,
  role,
  onLogout,
  onOpenAppointment,
}: {
  username: string
  role: string
  onLogout: () => void
  onOpenAppointment: (appointmentId: number, status: string) => void
}) {
  return (
    <div className="admin-topbar">
      <GlobalSearchBar onOpenAppointment={onOpenAppointment} />

      <div className="admin-topbar-actions">
        <NotificationBell />
        <DropdownMenu>
          <DropdownMenuTrigger className="admin-account-trigger">
            <span className="brand-mark" aria-hidden="true">
              {username.charAt(0).toUpperCase()}
            </span>
            <span className="admin-account-trigger-text">
              <strong>{username}</strong>
              <span className={`pill role-${role.toLowerCase()}`}>{role}</span>
            </span>
            <CaretDown size={14} weight="bold" className="admin-account-trigger-caret" aria-hidden="true" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => window.location.assign('/')}>
              <Globe size={18} weight="regular" />
              Patient site
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="danger" onSelect={onLogout}>
              <SignOut size={18} weight="regular" />
              Logout
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}
