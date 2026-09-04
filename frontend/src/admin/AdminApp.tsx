import { useEffect, useState } from 'react'
import {
  Buildings,
  CalendarCheck,
  CalendarPlus,
  ChartLineUp,
  GearSix,
  ShieldCheck,
  SignOut,
  Stethoscope,
  Tag,
  UsersThree,
} from '@phosphor-icons/react'
import { clearStaffToken, getStaffMe, getStaffToken, staffLogout } from '../api'
import type { Staff } from '../types'
import StaffLoginFlow from './StaffLoginFlow'
import DepartmentsPanel from './DepartmentsPanel'
import DoctorsPanel from './DoctorsPanel'
import AppointmentTypesPanel from './AppointmentTypesPanel'
import StaffAccountsPanel from './StaffAccountsPanel'
import PatientsPanel from './PatientsPanel'
import AppointmentsPanel from './AppointmentsPanel'
import NavMenu, { NavMenuToggle, type NavMenuItem } from './NavMenu'

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
  const [menuOpen, setMenuOpen] = useState(false)
  const [bookAppointmentSignal, setBookAppointmentSignal] = useState(0)

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

  function goTo(target: Section) {
    setSection(target)
  }

  function bookAppointment() {
    setSection('appointments')
    setBookAppointmentSignal((n) => n + 1)
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

  const isAdmin = staff.role === 'ADMIN'

  // Recurring schedule management is ADMIN-only per the RBAC design
  // (docs/WEB_EXPANSION_ARCHITECTURE.md section 8/section 10 item 3) --
  // enforced server-side regardless, but hiding the nav entry for a
  // STAFF session avoids a pointless 403 round trip for something
  // they're never authorized to do. Staff accounts management is the
  // same story (ADMIN only). Every other section here accepts either
  // role, matching the RBAC table.
  //
  // Analytics and Settings are deliberately marked `disabled` rather
  // than either being silently omitted or wired to a fake page: neither
  // has a backend or a panel behind it today, and inventing one wasn't
  // in scope here -- but the requested menu named them explicitly, so
  // they stay visible (with a plain "Coming soon" label) rather than a
  // silently incomplete menu or a dead-end click.
  const menuItems: NavMenuItem[] = [
    {
      key: 'appointments',
      label: 'Appointments',
      icon: <CalendarCheck size={28} weight="light" />,
      active: section === 'appointments',
      onSelect: () => goTo('appointments'),
    },
    {
      key: 'book-appointment',
      label: 'Book Appointment',
      icon: <CalendarPlus size={28} weight="light" />,
      onSelect: bookAppointment,
    },
    {
      key: 'doctors',
      label: 'Doctors',
      icon: <Stethoscope size={28} weight="light" />,
      active: section === 'doctors',
      onSelect: () => goTo('doctors'),
    },
    {
      key: 'patients',
      label: 'Patients',
      icon: <UsersThree size={28} weight="light" />,
      active: section === 'patients',
      onSelect: () => goTo('patients'),
    },
    {
      key: 'departments',
      label: 'Departments',
      icon: <Buildings size={28} weight="light" />,
      active: section === 'departments',
      onSelect: () => goTo('departments'),
    },
    {
      key: 'appointment-types',
      label: 'Appointment Types',
      icon: <Tag size={28} weight="light" />,
      active: section === 'appointment-types',
      onSelect: () => goTo('appointment-types'),
    },
    ...(isAdmin
      ? [
          {
            key: 'staff-accounts',
            label: 'Staff Accounts',
            icon: <ShieldCheck size={28} weight="light" />,
            active: section === 'staff-accounts',
            onSelect: () => goTo('staff-accounts'),
          } satisfies NavMenuItem,
        ]
      : []),
    {
      key: 'analytics',
      label: 'Analytics',
      icon: <ChartLineUp size={28} weight="light" />,
      disabled: true,
    },
    {
      key: 'settings',
      label: 'Settings',
      icon: <GearSix size={28} weight="light" />,
      disabled: true,
    },
    {
      key: 'logout',
      label: 'Logout',
      icon: <SignOut size={28} weight="light" />,
      onSelect: handleLogout,
    },
  ]

  return (
    <div className="page">
      <div className="admin-shell admin-shell-topnav">
        <header className="admin-topbar">
          <NavMenuToggle onClick={() => setMenuOpen(true)} />
          <div className="admin-topbar-brand">
            <span className="brand-mark">A</span>
            <span>
              <strong>Appointment Admin</strong>
              <span className="muted admin-topbar-role">
                {staff.username} · <span className={`pill role-${staff.role.toLowerCase()}`}>{staff.role}</span>
              </span>
            </span>
          </div>
        </header>

        <main className="admin-content admin-content-full">
          {section === 'appointments' && <AppointmentsPanel autoOpenCreateSignal={bookAppointmentSignal} />}
          {section === 'doctors' && <DoctorsPanel isAdmin={isAdmin} />}
          {section === 'departments' && <DepartmentsPanel isAdmin={isAdmin} />}
          {section === 'appointment-types' && <AppointmentTypesPanel isAdmin={isAdmin} />}
          {section === 'patients' && <PatientsPanel />}
          {section === 'staff-accounts' && isAdmin && <StaffAccountsPanel />}
        </main>
      </div>

      <NavMenu
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        items={menuItems}
        footerItems={[{ key: 'patient-site', label: 'Patient site', onSelect: () => window.location.assign('/') }]}
      />
    </div>
  )
}
