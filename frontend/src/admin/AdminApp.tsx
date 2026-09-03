import { useEffect, useState } from 'react'
import { clearStaffToken, getStaffMe, getStaffToken, staffLogout } from '../api'
import type { Staff } from '../types'
import StaffLoginFlow from './StaffLoginFlow'
import DepartmentsPanel from './DepartmentsPanel'
import DoctorsPanel from './DoctorsPanel'
import AppointmentTypesPanel from './AppointmentTypesPanel'
import StaffAccountsPanel from './StaffAccountsPanel'
import PatientsPanel from './PatientsPanel'
import AppointmentsPanel from './AppointmentsPanel'

type Section =
  | 'appointments'
  | 'doctors'
  | 'departments'
  | 'appointment-types'
  | 'patients'
  | 'staff-accounts'

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
        <p>Loading…</p>
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
  const sections: { key: Section; label: string; adminOnly?: boolean }[] = [
    { key: 'appointments', label: 'Appointments' },
    { key: 'doctors', label: 'Doctors' },
    { key: 'departments', label: 'Departments' },
    { key: 'appointment-types', label: 'Appointment Types' },
    { key: 'patients', label: 'Patients' },
    { key: 'staff-accounts', label: 'Staff Accounts', adminOnly: true },
  ]

  return (
    <div className="page">
      <div className="admin-shell">
        <nav className="admin-nav">
          <div className="admin-nav-header">
            <strong>Admin</strong>
            <span className="muted">
              {staff.username} ({staff.role})
            </span>
          </div>
          <ul>
            {sections
              .filter((s) => !s.adminOnly || staff.role === 'ADMIN')
              .map((s) => (
                <li key={s.key}>
                  <button
                    type="button"
                    className={s.key === section ? 'admin-nav-item active' : 'admin-nav-item'}
                    onClick={() => setSection(s.key)}
                  >
                    {s.label}
                  </button>
                </li>
              ))}
          </ul>
          <button type="button" className="link" onClick={handleLogout}>
            Log out
          </button>
          <a className="link" href="/">
            Patient site
          </a>
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
