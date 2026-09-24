import { Fragment, type ReactElement } from 'react'

export interface AdminSidebarItem {
  key: string
  label: string
  icon: ReactElement
  active?: boolean
  disabled?: boolean
  onSelect?: () => void
  // Optional section label (e.g. "MAIN", "MANAGE") shown above this
  // item when it differs from the previous item's group -- purely a
  // rendering grouping, not a route or a nested menu, so callers keep
  // passing the exact same flat list/navigation they always have.
  group?: string
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
export default function AdminSidebar({ items }: { items: AdminSidebarItem[] }) {
  return (
    <nav className="admin-sidebar" aria-label="Admin navigation">
      <div className="admin-sidebar-brand">
        <span className="brand-mark">A</span>
        <h1>Appointment Admin</h1>
      </div>

      <ul className="admin-sidebar-list">
        {items.map((item, index) => (
          <Fragment key={item.key}>
            {item.group && item.group !== items[index - 1]?.group && (
              <li className="admin-sidebar-group-label" aria-hidden="true">
                {item.group}
              </li>
            )}
            <li>
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
          </Fragment>
        ))}
      </ul>
    </nav>
  )
}
