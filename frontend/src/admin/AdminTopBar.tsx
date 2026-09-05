import { CaretDown, SignOut } from '@phosphor-icons/react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '../components/ui/dropdown-menu'

// Top-right account menu: the one place a logged-in staff member's
// identity and their Logout action live, replacing the old plain-text
// Logout entry that used to sit at the bottom of the left sidebar's nav
// list (per explicit feedback -- Logout reads as an account action, not
// a page to navigate to, so it belongs with the account's own name/role
// display, not mixed into the page nav).
export default function AdminTopBar({
  username,
  role,
  onLogout,
}: {
  username: string
  role: string
  onLogout: () => void
}) {
  return (
    <div className="admin-topbar">
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
          <DropdownMenuItem variant="danger" onSelect={onLogout}>
            <SignOut size={18} weight="regular" />
            Logout
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
