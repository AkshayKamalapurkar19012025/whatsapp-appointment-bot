import { useEffect, useState, type ReactElement } from 'react'
import { clearStaffToken, getStaffMe, getStaffToken, staffLogout } from '../api'
import type { Staff } from '../types'
import StaffLoginFlow from './StaffLoginFlow'
import DepartmentsPanel from './DepartmentsPanel'
import DoctorsPanel from './DoctorsPanel'
import AppointmentTypesPanel from './AppointmentTypesPanel'
import StaffAccountsPanel from './StaffAccountsPanel'
import PatientsPanel from './PatientsPanel'
import AppointmentsPanel from './AppointmentsPanel'
import {
  IconBuilding,
  IconCalendar,
  IconShieldUser,
  IconStethoscope,
  IconTag,
  IconUsers,
} from './icons'

type Section =
  | 'appointments'
  | 'doctors'
  | 'departments'
  | 'appointment-types'
  | 'patients'
  | 'staff-accounts'

// Nothing in this component clears staff/getStaffToken() when `section`
// changes -- switching sections is a plain in-memory state update, same
// component tree, same mounted session. The staff bearer token in
// localStorage (see api.ts) is completely untouched by navigation; it
// only ever gets cleared by an explicit Log out click or a 401 from the
// backend (session idle-timeout/absolute-expiry), both handled by
// handleLoggedIn/handleLogout below, not by which section is showing.
export default function AdminApp() {
  const [staff, setStaff] = useState<Staff | null>(null)
  const [checkingSession, setCheckingSession] = useState(true)
  const [section, setSection] = useState<Section>('appointments')

  useEffect(() => {
    if (!getStaffToken()) {
      setCheckingSession(false)
      return
    }
    getStaffMe()
      .then(setStaff)
      .catch(() => clearStaffToken())
      .finally(() => setCheckingSession(false))
  }, [])

  function handleLoggedIn() {
    getStaffMe()
      .then(setStaff)
      .catch(() => clearStaffToken())
  }

  async function handleLogout() {
    await staffLogout().catch(() => undefined)
    clearStaffToken()
    setStaff(null)
    setSection('appointments')
  }

  if (checkingSession) {
    return (
      <div className="page">
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      </div>
    )
  }

  if (!staff) {
    return (
      <div className="page">
        <StaffLoginFlow onLoggedIn={handleLoggedIn} />
      </div>
    )
  }

  // Recurring schedule management is ADMIN-only per the RBAC design
  // (docs/WEB_EXPANSION_ARCHITECTURE.md section 8/section 10 item 3) --
  // enforced server-side regardless, but hiding the nav entry for a
  // STAFF session avoids a pointless 403 round trip for something
  // they're never authorized to do. Staff accounts management is the
  // same story (ADMIN only). Every other section here accepts either
  // role, matching the RBAC table.
  const groups: { label: string; items: { key: Section; label: string; icon: ReactElement; adminOnly?: boolean }[] }[] = [
    {
      label: 'Scheduling',
      items: [
        { key: 'appointments', label: 'Appointments', icon: <IconCalendar /> },
        { key: 'doctors', label: 'Doctors', icon: <IconStethoscope /> },
        { key: 'departments', label: 'Departments', icon: <IconBuilding /> },
        { key: 'appointment-types', label: 'Appointment Types', icon: <IconTag /> },
      ],
    },
    {
      label: 'People',
      items: [
        { key: 'patients', label: 'Patients', icon: <IconUsers /> },
        { key: 'staff-accounts', label: 'Staff Accounts', icon: <IconShieldUser />, adminOnly: true },
      ],
    },
  ]

  return (
    <div className="page">
      <div className="admin-shell">
        <nav className="admin-nav">
          <div className="admin-nav-header">
            <strong>
              <span className="brand-mark">A</span>
              Appointment Admin
            </strong>
            <span className="muted">
              {staff.username} · <span className={`pill role-${staff.role.toLowerCase()}`}>{staff.role}</span>
            </span>
          </div>

          {groups.map((group) => {
            const visible = group.items.filter((i) => !i.adminOnly || staff.role === 'ADMIN')
            if (visible.length === 0) return null
            return (
              <div key={group.label}>
                <div className="admin-nav-group-label">{group.label}</div>
                <ul>
                  {visible.map((item) => (
                    <li key={item.key}>
                      <button
                        type="button"
                        className={item.key === section ? 'admin-nav-item active' : 'admin-nav-item'}
                        onClick={() => setSection(item.key)}
                      >
                        <span className="nav-icon">{item.icon}</span>
                        {item.label}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}

          <div className="admin-nav-footer">
            <button type="button" className="link" onClick={handleLogout}>
              Log out
            </button>
            <a className="link" href="/">
              Patient site
            </a>
          </div>
        </nav>

        <main className="admin-content">
          {section === 'appointments' && <AppointmentsPanel />}
          {section === 'doctors' && <DoctorsPanel isAdmin={staff.role === 'ADMIN'} />}
          {section === 'departments' && <DepartmentsPanel isAdmin={staff.role === 'ADMIN'} />}
          {section === 'appointment-types' && (
            <AppointmentTypesPanel isAdmin={staff.role === 'ADMIN'} />
          )}
          {section === 'patients' && <PatientsPanel />}
          {section === 'staff-accounts' && staff.role === 'ADMIN' && <StaffAccountsPanel />}
        </main>
      </div>
    </div>
  )
}
