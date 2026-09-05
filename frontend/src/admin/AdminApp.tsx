import { useEffect, useState } from 'react'
import {
  Buildings,
  CalendarCheck,
  CalendarPlus,
  ChartLineUp,
  Gauge,
  GearSix,
  ShieldCheck,
  Stethoscope,
  Tag,
  UsersThree,
} from '@phosphor-icons/react'
import { clearStaffToken, getStaffMe, getStaffToken, staffLogout } from '../api'
import type { Staff } from '../types'
import StaffLoginFlow from './StaffLoginFlow'
import DashboardPanel from './DashboardPanel'
import DepartmentsPanel from './DepartmentsPanel'
import DoctorsPanel from './DoctorsPanel'
import AppointmentTypesPanel from './AppointmentTypesPanel'
import StaffAccountsPanel from './StaffAccountsPanel'
import PatientsPanel from './PatientsPanel'
import AppointmentsPanel from './AppointmentsPanel'
import BookAppointmentPanel from './BookAppointmentPanel'
import AdminSidebar, { type AdminSidebarItem } from './AdminSidebar'
import AdminTopBar from './AdminTopBar'

type Section =
  | 'dashboard'
  | 'appointments'
  | 'book-appointment'
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
  const [section, setSection] = useState<Section>('dashboard')

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
    setSection('dashboard')
  }

  function goTo(target: Section) {
    setSection(target)
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
  const menuItems: AdminSidebarItem[] = [
    {
      key: 'dashboard',
      label: 'Dashboard',
      icon: <Gauge size={20} weight="regular" />,
      active: section === 'dashboard',
      onSelect: () => goTo('dashboard'),
    },
    {
      key: 'appointments',
      label: 'Appointments',
      icon: <CalendarCheck size={20} weight="regular" />,
      active: section === 'appointments',
      onSelect: () => goTo('appointments'),
    },
    {
      key: 'book-appointment',
      label: 'Book Appointment',
      icon: <CalendarPlus size={20} weight="regular" />,
      active: section === 'book-appointment',
      onSelect: () => goTo('book-appointment'),
    },
    {
      key: 'doctors',
      label: 'Doctors',
      icon: <Stethoscope size={20} weight="regular" />,
      active: section === 'doctors',
      onSelect: () => goTo('doctors'),
    },
    {
      key: 'patients',
      label: 'Patients',
      icon: <UsersThree size={20} weight="regular" />,
      active: section === 'patients',
      onSelect: () => goTo('patients'),
    },
    {
      key: 'departments',
      label: 'Departments',
      icon: <Buildings size={20} weight="regular" />,
      active: section === 'departments',
      onSelect: () => goTo('departments'),
    },
    {
      key: 'appointment-types',
      label: 'Appointment Types',
      icon: <Tag size={20} weight="regular" />,
      active: section === 'appointment-types',
      onSelect: () => goTo('appointment-types'),
    },
    ...(isAdmin
      ? [
          {
            key: 'staff-accounts',
            label: 'Staff Accounts',
            icon: <ShieldCheck size={20} weight="regular" />,
            active: section === 'staff-accounts',
            onSelect: () => goTo('staff-accounts'),
          } satisfies AdminSidebarItem,
        ]
      : []),
    {
      key: 'analytics',
      label: 'Analytics',
      icon: <ChartLineUp size={20} weight="regular" />,
      disabled: true,
    },
    {
      key: 'settings',
      label: 'Settings',
      icon: <GearSix size={20} weight="regular" />,
      disabled: true,
    },
  ]

  return (
    <div className="page">
      <div className="admin-shell">
        <AdminSidebar
          items={menuItems}
          footerItems={[{ key: 'patient-site', label: 'Patient site', onSelect: () => window.location.assign('/') }]}
        />

        <main className="admin-content">
          <AdminTopBar username={staff.username} role={staff.role} onLogout={handleLogout} />

          {section === 'dashboard' && (
            <DashboardPanel
              staffName={staff.username}
              onBookAppointment={() => goTo('book-appointment')}
              onGoToDoctors={() => goTo('doctors')}
              onGoToPatients={() => goTo('patients')}
            />
          )}
          {section === 'appointments' && <AppointmentsPanel onBookAppointment={() => goTo('book-appointment')} />}
          {section === 'book-appointment' && (
            <BookAppointmentPanel onViewAppointments={() => goTo('appointments')} />
          )}
          {section === 'doctors' && <DoctorsPanel isAdmin={isAdmin} />}
          {section === 'departments' && <DepartmentsPanel isAdmin={isAdmin} />}
          {section === 'appointment-types' && <AppointmentTypesPanel isAdmin={isAdmin} />}
          {section === 'patients' && <PatientsPanel />}
          {section === 'staff-accounts' && isAdmin && <StaffAccountsPanel />}
        </main>
      </div>
    </div>
  )
}
