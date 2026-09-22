import { useEffect, useRef, useState } from 'react'
import { Bell } from '@phosphor-icons/react'
import { ApiError, getNotifications, markAllNotificationsRead, markNotificationRead } from '../api'
import type { StaffNotification } from '../types'
import { formatDateTime } from '../format'

const POLL_INTERVAL_MS = 30_000

// Master spec section 15: "no notification/alert bell or center
// exists ... not the broader 'new patient arrived / lab result
// available / prescription ready' event stream section 15 describes."
// Hospital-wide, not per-staff -- see migrations/0044_notification_
// center.sql for why. Polls the same way QueueSection already does
// (20s there; 30s here since this is a passive background badge, not
// an actively-worked screen).
export default function NotificationBell() {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<StaffNotification[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  function load() {
    getNotifications()
      .then((result) => {
        setItems(result.items)
        setUnreadCount(result.unread_count)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load notifications'))
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  function handleMarkRead(notification: StaffNotification) {
    if (notification.read_at) return
    markNotificationRead(notification.id)
      .then(() => load())
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not update this notification'))
  }

  function handleMarkAllRead() {
    markAllNotificationsRead()
      .then(() => load())
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not update notifications'))
  }

  return (
    <div className="notification-bell" ref={containerRef}>
      <button
        type="button"
        className="notification-bell-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ''}`}
      >
        <Bell size={20} weight={unreadCount > 0 ? 'fill' : 'regular'} />
        {unreadCount > 0 && <span className="notification-bell-badge">{unreadCount > 9 ? '9+' : unreadCount}</span>}
      </button>

      {open && (
        <div className="notification-bell-dropdown">
          <div className="notification-bell-header">
            <strong>Notifications</strong>
            {unreadCount > 0 && (
              <button type="button" className="link" onClick={handleMarkAllRead}>
                Mark all read
              </button>
            )}
          </div>

          {error && <p className="error">{error}</p>}

          {items.length === 0 && !error && <p className="muted notification-bell-empty">Nothing yet.</p>}

          <ul className="notification-bell-list">
            {items.map((n) => (
              <li
                key={n.id}
                className={n.read_at ? 'read' : 'unread'}
                onClick={() => handleMarkRead(n)}
              >
                <span>{n.message}</span>
                <span className="muted">{formatDateTime(n.created_at)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
