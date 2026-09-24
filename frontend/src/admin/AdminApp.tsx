import { useEffect, useState } from 'react'
import {
  Buildings,
  CalendarCheck,
  ChartLineUp,
  ClockCounterClockwise,
  CreditCard,
  CurrencyInr,
  Flask,
  Gauge,
  GearSix,
  Gift,
  Pill,
  Receipt,
  ShieldCheck,
  Stethoscope,
  Tag,
  UsersFour,
  UsersThree,
} from '@phosphor-icons/react'
import { clearStaffToken, getStaffMe, getStaffToken, staffLogout } from '../api'
import type { Staff, StaffRole } from '../types'
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
import AuditLogPanel from './AuditLogPanel'
import DepartmentQueuePanel from './DepartmentQueuePanel'
import BillingHistoryPanel from './BillingHistoryPanel'
import PaymentHistoryPanel from './PaymentHistoryPanel'
import WaitingTimeAnalyticsPanel from './WaitingTimeAnalyticsPanel'
import LabRadiologyWorklistPanel from './LabRadiologyWorklistPanel'
import AdminSidebar, { type AdminSidebarItem } from './AdminSidebar'
import AdminTopBar from './AdminTopBar'

type Section =
  | 'dashboard'
  | 'appointments'
  | 'book-appointment'
  | 'queue'
  | 'consultation'
  | 'doctors'
  | 'department-queue'
  | 'departments'
  | 'appointment-types'
  | 'patients'
  | 'staff-accounts'
  | 'audit-log'
  | 'billing'
  | 'billing-history'
  | 'payment-history'
  | 'waiting-time-analytics'
  | 'pharmacy'
  | 'packages'
  | 'lab-worklist'

// Master spec audit Principle 5 ("Reception/Nurse/Doctor/Lab/
// Radiology/Pharmacist/Cashier/Admin see different workflows"):
// role-differentiated sidebar visibility + landing screen, now that
// migrations/0043_role_based_access.sql makes these 8 roles real,
// loggable-in accounts rather than schema-only labels. ADMIN and
// STAFF are deliberately absent from ROLE_VISIBLE_SECTIONS below --
// both keep today's unrestricted "see everything but Staff Accounts/
// Audit Log" behavior (STAFF remains the generalist fallback for any
// account not yet assigned one of the six specific roles), so the
// lookup below simply falls through to "show everything" for them.
//
// This is sidebar/routing UX, not a new access-control boundary: every
// bare-STAFF-auth endpoint (vitals, orders, prescriptions, routine
// payment collection) stays reachable by any authenticated session
// regardless of what's in this table, exactly as migrations/0043's own
// docstring says it should. The three actions that ARE real RBAC today
// (pharmacy.manage_stock, the bill.*/appointment.*_payment set,
// consultation.amend) are separately gated below by the matching role,
// not by this table -- see canManageStock/canManageBilling/
// canAmendConsultation.
const ROLE_VISIBLE_SECTIONS: Partial<Record<StaffRole, Set<Section>>> = {
  RECEPTIONIST: new Set<Section>([
    'dashboard', 'appointments', 'book-appointment', 'queue', 'consultation', 'department-queue', 'patients',
  ]),
  NURSE: new Set<Section>([
    'dashboard', 'department-queue', 'appointments', 'book-appointment', 'queue', 'consultation', 'patients',
  ]),
  DOCTOR: new Set<Section>([
    'dashboard', 'appointments', 'book-appointment', 'queue', 'consultation', 'department-queue', 'patients',
  ]),
  // Lab Worklist (LabRadiologyWorklistPanel.tsx) closes the gap the
  // comment here used to flag: a cross-patient view of every open LAB/
  // RADIOLOGY order, filterable by type, with the same result-entry
  // form ConsultationWorkspace's Orders tab uses -- LAB_TECH's first
  // real, differentiated workflow rather than the doctor's own screens
  // reused minus a section.
  LAB_TECH: new Set<Section>(['dashboard', 'lab-worklist', 'appointments', 'patients']),
  PHARMACIST: new Set<Section>(['dashboard', 'pharmacy', 'patients']),
  BILLING: new Set<Section>([
    'dashboard', 'appointments', 'patients', 'billing', 'billing-history', 'payment-history',
  ]),
}

// Where each role lands right after login, instead of the generic
// Dashboard -- the cheapest, most visible part of "different
// workflows": a receptionist opens the app already on Appointments, a
// pharmacist on Pharmacy, not one more click away from their own job.
const ROLE_LANDING_SECTION: Partial<Record<StaffRole, Section>> = {
  RECEPTIONIST: 'appointments',
  NURSE: 'department-queue',
  DOCTOR: 'department-queue',
  PHARMACIST: 'pharmacy',
  BILLING: 'billing-history',
  LAB_TECH: 'lab-worklist',
}

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

  // Shared by both places a fresh `staff` arrives (session restore on
  // load, and a just-completed login) -- lands the session on its
  // role's own default screen (ROLE_LANDING_SECTION) rather than
  // always Dashboard. A role with no entry there (ADMIN/STAFF) keeps
  // today's Dashboard-first behavior unchanged.
  function applyStaff(result: Staff) {
    setStaff(result)
    setSection(ROLE_LANDING_SECTION[result.role] ?? 'dashboard')
  }

  useEffect(() => {
    if (!getStaffToken()) {
      setCheckingSession(false)
      return
    }
    getStaffMe()
      .then(applyStaff)
      .catch(() => clearStaffToken())
      .finally(() => setCheckingSession(false))
  }, [])

  function handleLoggedIn() {
    getStaffMe()
      .then(applyStaff)
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

  // GlobalSearchBar's appointment-result click (master spec section
  // 14): jump straight into the visit in progress when there is one,
  // otherwise land on the Appointments list, which can find/filter to
  // it from there -- there's no deep-link-by-id view for a PENDING/
  // CONFIRMED/COMPLETED appointment to jump into directly the way
  // ConsultationWorkspace is for a CHECKED_IN one.
  function goToSearchResult(appointmentId: number, status: string) {
    if (status === 'CHECKED_IN') {
      goToConsultation(appointmentId)
    } else {
      goTo('appointments')
    }
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
  // Real RBAC, server-enforced regardless of what these booleans gate
  // client-side -- see PharmacyPanel.tsx/AppointmentBillingPanel.tsx/
  // ConsultationWorkspace.tsx/PrescriptionPanel.tsx for where each is
  // actually used. STAFF holds vitals.record/consultation.write/
  // order.create/prescription.create (migrations/0048_clinical_rbac_
  // permissions.sql) alongside NURSE/DOCTOR -- unlike pharmacy.
  // manage_stock/bill.*/consultation.amend above, which STAFF does
  // NOT hold (migrations/0043_role_based_access.sql's own pattern).
  const canManageStock = isAdmin || staff.role === 'PHARMACIST'
  const canManageBilling = isAdmin || staff.role === 'BILLING'
  const canAmendConsultation = isAdmin || staff.role === 'DOCTOR'
  const canRecordVitals = isAdmin || staff.role === 'STAFF' || staff.role === 'NURSE' || staff.role === 'DOCTOR'
  const canWriteConsultation = isAdmin || staff.role === 'STAFF' || staff.role === 'DOCTOR'
  const canCreateOrders = canWriteConsultation
  const canCreatePrescriptions = canWriteConsultation
  // order.result (migrations/0051) -- ADMIN/STAFF/DOCTOR keep the same
  // access as every other clinical-documentation gate; LAB_TECH is the
  // new addition, the Lab Worklist's whole reason to exist.
  const canRecordOrderResults = isAdmin || staff.role === 'STAFF' || staff.role === 'DOCTOR' || staff.role === 'LAB_TECH'
  const visibleSections = ROLE_VISIBLE_SECTIONS[staff.role]

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
      key: 'department-queue',
      label: 'Department Queue',
      icon: <UsersFour size={20} weight="regular" />,
      active: section === 'department-queue',
      onSelect: () => goTo('department-queue'),
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
    {
      key: 'lab-worklist',
      label: 'Lab Worklist',
      icon: <Flask size={20} weight="regular" />,
      active: section === 'lab-worklist',
      onSelect: () => goTo('lab-worklist'),
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
          {
            key: 'audit-log',
            label: 'Audit Log',
            icon: <ClockCounterClockwise size={20} weight="regular" />,
            active: section === 'audit-log',
            onSelect: () => goTo('audit-log'),
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
      key: 'billing-history',
      label: 'Billing History',
      icon: <Receipt size={20} weight="regular" />,
      active: section === 'billing-history',
      onSelect: () => goTo('billing-history'),
      group: 'Reports',
    },
    {
      key: 'payment-history',
      label: 'Payment History',
      icon: <CreditCard size={20} weight="regular" />,
      active: section === 'payment-history',
      onSelect: () => goTo('payment-history'),
      group: 'Reports',
    },
    {
      key: 'waiting-time-analytics',
      label: 'Waiting-Time Analytics',
      icon: <ChartLineUp size={20} weight="regular" />,
      active: section === 'waiting-time-analytics',
      onSelect: () => goTo('waiting-time-analytics'),
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

  // 'settings' is a disabled, "Coming soon" placeholder with no real
  // content behind it -- not a Section worth restricting, so every
  // role keeps seeing it regardless of what's in ROLE_VISIBLE_SECTIONS.
  const visibleMenuItems = visibleSections
    ? menuItems.filter((item) => item.key === 'settings' || visibleSections.has(item.key as Section))
    : menuItems

  return (
    <div className="page">
      <div className="admin-shell">
        <AdminSidebar items={visibleMenuItems} />

        <main className="admin-content">
          <AdminTopBar
            username={staff.username}
            role={staff.role}
            onLogout={handleLogout}
            onOpenAppointment={goToSearchResult}
          />

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
              canAmendConsultation={canAmendConsultation}
              canManageBilling={canManageBilling}
              canRecordVitals={canRecordVitals}
              canWriteConsultation={canWriteConsultation}
              canCreateOrders={canCreateOrders}
              canCreatePrescriptions={canCreatePrescriptions}
              onBack={() => goTo('queue')}
            />
          )}
          {section === 'doctors' && <DoctorsPanel key={navResetKey} isAdmin={isAdmin} onGoToQueue={goToQueueForDoctor} />}
          {section === 'department-queue' && (
            <DepartmentQueuePanel key={navResetKey} onOpenConsultation={goToConsultation} />
          )}
          {section === 'departments' && <DepartmentsPanel key={navResetKey} isAdmin={isAdmin} />}
          {section === 'appointment-types' && <AppointmentTypesPanel key={navResetKey} isAdmin={isAdmin} />}
          {section === 'patients' && <PatientsPanel key={navResetKey} />}
          {section === 'staff-accounts' && isAdmin && <StaffAccountsPanel key={navResetKey} />}
          {section === 'audit-log' && isAdmin && <AuditLogPanel key={navResetKey} />}
          {section === 'billing' && <BillingPanel key={navResetKey} />}
          {section === 'billing-history' && <BillingHistoryPanel key={navResetKey} />}
          {section === 'payment-history' && <PaymentHistoryPanel key={navResetKey} />}
          {section === 'waiting-time-analytics' && <WaitingTimeAnalyticsPanel key={navResetKey} />}
          {section === 'pharmacy' && <PharmacyPanel key={navResetKey} canManageStock={canManageStock} />}
          {section === 'packages' && <PackagesPanel key={navResetKey} isAdmin={isAdmin} />}
          {section === 'lab-worklist' && (
            <LabRadiologyWorklistPanel key={navResetKey} canRecordResults={canRecordOrderResults} />
          )}
        </main>
      </div>
    </div>
  )
}
