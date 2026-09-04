import type { ReactElement } from 'react'

export interface AdminSidebarItem {
  key: string
  label: string
  icon: ReactElement
  active?: boolean
  disabled?: boolean
  onSelect?: () => void
}

// A plain, always-visible left sidebar -- no toggle, no open/close
// animation, no hover-revealed preview panel. Replaces the earlier
// hamburger-triggered overlay/drawer menu per explicit feedback: a
// permanent sidebar is simpler and more conventional for this admin
// dashboard. Icons are always visible (not hover-only); hovering a row
// is just a background highlight, the same treatment every other
// clickable row in this app already gets (.data-table tbody tr:hover,
// .tag-list .link, etc.) -- nothing here needs its own bespoke
// animation.
export default function AdminSidebar({
  items,
  footerItems,
  username,
  role,
}: {
  items: AdminSidebarItem[]
  footerItems?: { key: string; label: string; onSelect: () => void }[]
  username: string
  role: string
}) {
  return (
    <nav className="admin-sidebar" aria-label="Admin navigation">
      <div className="admin-sidebar-brand">
        <span className="brand-mark">A</span>
        <span>
          <strong>Appointment Admin</strong>
          <span className="muted admin-sidebar-role">
            {username} · <span className={`pill role-${role.toLowerCase()}`}>{role}</span>
          </span>
        </span>
      </div>

      <ul className="admin-sidebar-list">
        {items.map((item) => (
          <li key={item.key}>
            <button
              type="button"
              className={`admin-sidebar-item${item.active ? ' active' : ''}${item.disabled ? ' disabled' : ''}`}
              disabled={item.disabled}
              onClick={item.onSelect}
            >
              <span className="admin-sidebar-item-icon" aria-hidden="true">
                {item.icon}
              </span>
              <span className="admin-sidebar-item-label">
                {item.label}
                {item.disabled && <span className="admin-sidebar-item-badge">Coming soon</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {footerItems && footerItems.length > 0 && (
        <div className="admin-sidebar-footer">
          {footerItems.map((f) => (
            <button key={f.key} type="button" className="link" onClick={f.onSelect}>
              {f.label}
            </button>
          ))}
        </div>
      )}
    </nav>
  )
}
