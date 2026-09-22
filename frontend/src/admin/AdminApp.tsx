import { useEffect, useState } from 'react'
import {
  Buildings,
  CalendarCheck,
  ChartLineUp,
  CurrencyInr,
  Gauge,
  GearSix,
  Gift,
  Pill,
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
import BillingPanel from './BillingPanel'
import BookAppointmentPanel from './BookAppointmentPanel'
import QueuePanel from './QueuePanel'
import ConsultationWorkspace from './ConsultationWorkspace'
import PharmacyPanel from './PharmacyPanel'
import PackagesPanel from './PackagesPanel'
import AdminSidebar, { type AdminSidebarItem } from './AdminSidebar'
import AdminTopBar from './AdminTopBar'

type Section =
  | 'dashboard'
  | 'appointments'
  | 'book-appointment'
  | 'queue'
  | 'consultation'
  | 'doctors'
  | 'departments'
  | 'appointment-types'
  | 'patients'
  | 'staff-accounts'
  | 'billing'
  | 'pharmacy'
  | 'packages'

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
  // Bumped on every goTo(), including clicking a sidebar item that's
  // already active. Several panels keep their own "drill-in" state
  // (DoctorsPanel's selectedDoctor swaps the whole panel for
  // DoctorWorkspace; AppointmentTypesPanel's view drawer, various
  // modals) that setSection(target) alone can't reset when target ===
  // section -- clicking "Doctors" while already inside a doctor's
  // workspace was a no-op, so there was no way back to the directory
  // except browser back. Keying each panel by this counter forces a
  // clean remount on every nav click, same as switching sections
  // already does naturally.
  const [navResetKey, setNavResetKey] = useState(0)
  // Set right before navigating to 'book-appointment' from the "+ New
  // OPD Visit > Register New Patient" entry (AppointmentsPanel's
  // onRegisterNewPatient) -- tells BookAppointmentPanel to open its own
  // "+ Register new patient" modal once its find/register step is
  // showing, instead of a second, separate patient-registration screen.
  // navResetKey's remount-on-every-goTo means this never needs
  // resetting back to false: the next 'book-appointment' nav (from
  // "Book Appointment"/"Walk-in Registration", which set it false right
  // before navigating) always starts BookAppointmentPanel fresh.
  const [autoOpenRegisterOnBook, setAutoOpenRegisterOnBook] = useState(false)
  // Which appointment ConsultationWorkspace opens for -- set right
  // before navigating to 'consultation' (goToConsultation below), same
  // handoff pattern as autoOpenRegisterOnBook above. Passed as plain
  // component state rather than the localStorage handoff
  // goToQueueForDoctor uses, since this is always set synchronously by
  // the same click that navigates here (there's no "arrive at this
  // section from somewhere else" case to survive a remount for, unlike
  // the queue's last-viewed-doctor convenience).
  const [consultationAppointmentId, setConsultationAppointmentId] = useState<number | null>(null)

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
    setNavResetKey((n) => n + 1)
  }

  function goToRegisterNewPatient() {
    setAutoOpenRegisterOnBook(true)
    goTo('book-appointment')
  }

  function goToBookAppointment() {
    setAutoOpenRegisterOnBook(false)
    goTo('book-appointment')
  }

  // Shared by DoctorsPanel's "View queue" and AppointmentsPanel's
  // per-row "Open Queue" action -- both just want "take me to this
  // doctor's live queue", same QueuePanel.tsx destination and the same
  // localStorage handoff it already reads on mount.
  function goToQueueForDoctor(doctorId: number) {
    try {
      localStorage.setItem('admin_queue_panel_last_doctor_id', String(doctorId))
    } catch {
      // Best-effort only, same as QueuePanel's own write to this key.
    }
    goTo('queue')
  }

  function goToConsultation(appointmentId: number) {
    setConsultationAppointmentId(appointmentId)
    goTo('consultation')
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
  // silently incomplete menu or a dead-end click. Billing is the one
  // "Reports" entry that's real (GET /dashboard/billing), so it's not
  // marked disabled.
  const menuItems: AdminSidebarItem[] = [
    {
      key: 'dashboard',
      label: 'Dashboard',
      icon: <Gauge size={20} weight="regular" />,
      active: section === 'dashboard',
      onSelect: () => goTo('dashboard'),
      group: 'Main',
    },
    {
      key: 'appointments',
      label: 'Appointments',
      icon: <CalendarCheck size={20} weight="regular" />,
      // Also "active" while parked on Book Appointment or Queue -- both
      // are reached only as actions from inside this workspace now (the
      // "+ New OPD Visit" dropdown and the header's Queue button/per-row
      // "Open Queue"), not separate top-level destinations, so the nav
      // shouldn't go dark while a staff member is mid-booking or
      // watching a doctor's queue.
      active:
        section === 'appointments' ||
        section === 'book-appointment' ||
        section === 'queue' ||
        section === 'consultation',
      onSelect: () => goTo('appointments'),
      group: 'Main',
    },
    {
      key: 'doctors',
      label: 'Doctors',
      icon: <Stethoscope size={20} weight="regular" />,
      active: section === 'doctors',
      onSelect: () => goTo('doctors'),
      group: 'Manage',
    },
    {
      key: 'patients',
      label: 'Patients',
      icon: <UsersThree size={20} weight="regular" />,
      active: section === 'patients',
      onSelect: () => goTo('patients'),
      group: 'Manage',
    },
    {
      key: 'departments',
      label: 'Departments',
      icon: <Buildings size={20} weight="regular" />,
      active: section === 'departments',
      onSelect: () => goTo('departments'),
      group: 'Manage',
    },
    {
      key: 'appointment-types',
      label: 'Appointment Types',
      icon: <Tag size={20} weight="regular" />,
      active: section === 'appointment-types',
      onSelect: () => goTo('appointment-types'),
      group: 'Manage',
    },
    {
      key: 'pharmacy',
      label: 'Pharmacy',
      icon: <Pill size={20} weight="regular" />,
      active: section === 'pharmacy',
      onSelect: () => goTo('pharmacy'),
      group: 'Manage',
    },
    {
      key: 'packages',
      label: 'Packages',
      icon: <Gift size={20} weight="regular" />,
      active: section === 'packages',
      onSelect: () => goTo('packages'),
      group: 'Manage',
    },
    ...(isAdmin
      ? [
          {
            key: 'staff-accounts',
            label: 'Staff Accounts',
            icon: <ShieldCheck size={20} weight="regular" />,
            active: section === 'staff-accounts',
            onSelect: () => goTo('staff-accounts'),
            group: 'Admin',
          } satisfies AdminSidebarItem,
        ]
      : []),
    {
      key: 'billing',
      label: 'Billing',
      icon: <CurrencyInr size={20} weight="regular" />,
      active: section === 'billing',
      onSelect: () => goTo('billing'),
      group: 'Reports',
    },
    {
      key: 'analytics',
      label: 'Analytics',
      icon: <ChartLineUp size={20} weight="regular" />,
      disabled: true,
      group: 'Reports',
    },
    {
      key: 'settings',
      label: 'Settings',
      icon: <GearSix size={20} weight="regular" />,
      disabled: true,
      group: 'Settings',
    },
  ]

  return (
    <div className="page">
      <div className="admin-shell">
        <AdminSidebar items={menuItems} />

        <main className="admin-content">
          <AdminTopBar username={staff.username} role={staff.role} onLogout={handleLogout} />

          {section === 'dashboard' && (
            <DashboardPanel
              key={navResetKey}
              staffName={staff.username}
              onBookAppointment={goToBookAppointment}
              onGoToDoctors={() => goTo('doctors')}
              onGoToPatients={() => goTo('patients')}
            />
          )}
          {section === 'appointments' && (
            <AppointmentsPanel
              key={navResetKey}
              onBookAppointment={goToBookAppointment}
              onRegisterNewPatient={goToRegisterNewPatient}
              onGoToQueue={goToQueueForDoctor}
              onViewQueue={() => goTo('queue')}
              isAdmin={isAdmin}
            />
          )}
          {section === 'book-appointment' && (
            <BookAppointmentPanel
              key={navResetKey}
              onViewAppointments={() => goTo('appointments')}
              onGoToQueue={goToQueueForDoctor}
              onGoToPatients={() => goTo('patients')}
              autoOpenRegister={autoOpenRegisterOnBook}
            />
          )}
          {section === 'queue' && (
            <QueuePanel key={navResetKey} onBack={() => goTo('appointments')} onOpenConsultation={goToConsultation} />
          )}
          {section === 'consultation' && consultationAppointmentId !== null && (
            <ConsultationWorkspace
              key={navResetKey}
              appointmentId={consultationAppointmentId}
              isAdmin={isAdmin}
              onBack={() => goTo('queue')}
            />
          )}
          {section === 'doctors' && <DoctorsPanel key={navResetKey} isAdmin={isAdmin} onGoToQueue={goToQueueForDoctor} />}
          {section === 'departments' && <DepartmentsPanel key={navResetKey} isAdmin={isAdmin} />}
          {section === 'appointment-types' && <AppointmentTypesPanel key={navResetKey} isAdmin={isAdmin} />}
          {section === 'patients' && <PatientsPanel key={navResetKey} />}
          {section === 'staff-accounts' && isAdmin && <StaffAccountsPanel key={navResetKey} />}
          {section === 'billing' && <BillingPanel key={navResetKey} />}
          {section === 'pharmacy' && <PharmacyPanel key={navResetKey} isAdmin={isAdmin} />}
          {section === 'packages' && <PackagesPanel key={navResetKey} isAdmin={isAdmin} />}
        </main>
      </div>
    </div>
  )
}
