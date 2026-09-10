import { Fragment, useEffect, useState } from 'react'
import { ArrowLeft, Buildings, CaretDown, DotsThree, Tag, UserCircle } from '@phosphor-icons/react'
import {
  ApiError,
  assignAppointmentTypeToDoctor,
  assignDoctorToDepartment,
  cancelAdminAppointment,
  completeAdminAppointment,
  confirmAdminAppointment,
  createDoctorBlock,
  createDoctorSchedule,
  deleteDoctorBlock,
  deleteDoctorSchedule,
  getDoctorBlocks,
  getDoctorDepartments,
  getDoctorScheduleAdmin,
  listAdminAppointments,
  listAppointmentTypeCatalog,
  listAppointmentTypesForDoctor,
  listDepartments,
  markArrivedAdmin,
  noShowAdminAppointment,
  rejectAdminAppointment,
  removeAppointmentTypeFromDoctor,
  removeDoctorFromDepartment,
  rescheduleAdminAppointment,
  setDoctorActive,
  updateDoctorAppointmentType,
  updateDoctorSlotSettings,
  visitAdminAppointment,
} from '../api'
import type {
  AdminAppointment,
  AppointmentType,
  AppointmentTypeSummary,
  Department,
  Doctor,
  DoctorBlockEntry,
  DoctorDepartmentAssignment,
  DoctorScheduleEntry,
  Slot,
} from '../types'
import { describeArrival, formatDate, formatTime, formatTimeOfDay, doctorSummaryLine } from '../format'
import DoctorAvatar from '../DoctorAvatar'
import DepartmentChip from '../DepartmentChip'
import { departmentIcon } from '../departmentIcon'
import AdminDatePicker from './AdminDatePicker'
import AdminSlotPicker from './AdminSlotPicker'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../components/ui/alert-dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../components/ui/dropdown-menu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import { TimeCombobox } from '../components/ui/time-combobox'
import DoctorProfileSection from './DoctorProfileSection'
import AppointmentDetailsModal from './AppointmentDetailsModal'
import { AppointmentActionButtons, buildAppointmentActions, type AppointmentActionHandlers } from './AppointmentActions'
import { DAY_NAMES, dateToDayOfWeek, generateDaySlots, isoDateToday } from './doctorSchedule'

const ALL_FILTER_VALUE = '__all__'

type WorkspaceTab = 'overview' | 'appointments' | 'schedule' | 'blocks' | 'departments' | 'types' | 'profile'

// The four operational tabs stay equally-weighted, top-level buttons --
// each answers a different day-to-day question ("what's happening
// today", "who's coming", "when is the doctor available/unavailable").
// Departments/Appointment types/Profile are configuration, changed
// rarely, not something that needs to compete for the same visual
// weight -- grouped under one "More" menu instead of three more equal
// tabs alongside the four operational ones. Internally these are still
// the same seven `tab` values/components as before; only how they're
// reached changed.
const PRIMARY_TABS: { key: WorkspaceTab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'appointments', label: 'Appointments' },
  { key: 'schedule', label: 'Schedule' },
  { key: 'blocks', label: 'Time off' },
]

const MORE_TABS: { key: WorkspaceTab; label: string; icon: React.ReactNode }[] = [
  { key: 'departments', label: 'Departments', icon: <Buildings size={15} /> },
  { key: 'types', label: 'Appointment types', icon: <Tag size={15} /> },
  { key: 'profile', label: 'Profile', icon: <UserCircle size={15} /> },
]

// The doctor workspace -- everything about one doctor, one level below
// the Doctors directory (DoctorsPanel.tsx). Replaces the old
// DoctorDetail.tsx: same underlying sections (schedule, time off,
// departments, appointment types, profile all keep their existing
// APIs/behavior below, moved here unchanged), reorganized around one
// "what's happening today" Overview instead of Profile being the
// default tab and Queue/Upcoming competing equally with configuration
// tabs like Departments.
export default function DoctorWorkspace({
  doctor,
  isAdmin,
  onBack,
  onGoToQueue,
}: {
  doctor: Doctor
  isAdmin: boolean
  onBack: () => void
  onGoToQueue?: (doctorId: number) => void
}) {
  const [tab, setTab] = useState<WorkspaceTab>('overview')
  const [deactivateOpen, setDeactivateOpen] = useState(false)
  const [deactivating, setDeactivating] = useState(false)
  const [deactivateError, setDeactivateError] = useState<string | null>(null)
  // Set by ScheduleSection's own onDirtyChange while its working-hours
  // form has in-progress, not-yet-saved changes -- guards the two
  // navigation actions this component itself controls (switching tabs,
  // going back to the Doctors directory) so neither silently discards
  // them. Switching to a *different* doctor happens one level up (the
  // Doctors directory list), outside what this component can guard.
  const [scheduleFormDirty, setScheduleFormDirty] = useState(false)

  function confirmDiscardIfDirty(): boolean {
    if (!scheduleFormDirty) return true
    return window.confirm('You have unsaved working hours — discard changes?')
  }

  function guardedSetTab(next: WorkspaceTab) {
    if (next === tab) return
    if (tab === 'schedule' && !confirmDiscardIfDirty()) return
    setTab(next)
  }

  function guardedOnBack() {
    if (tab === 'schedule' && !confirmDiscardIfDirty()) return
    onBack()
  }

  async function confirmDeactivate() {
    setDeactivating(true)
    setDeactivateError(null)
    try {
      await setDoctorActive(doctor.id, false)
      setDeactivateOpen(false)
      // A deactivated doctor drops out of every listing (see
      // update_doctor_active's docstring in app/api/doctors.py) --
      // nothing left in this workspace to show, so go back to the
      // directory rather than leaving the admin on a page for a
      // doctor that no longer appears anywhere.
      onBack()
    } catch (err) {
      setDeactivateError(err instanceof ApiError ? err.message : 'Could not deactivate doctor')
    } finally {
      setDeactivating(false)
    }
  }

  return (
    <div>
      <button type="button" className="link doctor-workspace-back" onClick={guardedOnBack}>
        <ArrowLeft size={15} weight="bold" /> Doctors
      </button>

      <div className="doctor-workspace-header">
        <DoctorAvatar photoUrl={doctor.photo_url} name={doctor.name} size={72} />
        <div className="doctor-workspace-header-body">
          <h2>{doctor.name}</h2>
          {/* Compact by design (spec: don't repeat detail in the
              header that the body already shows) -- specialization
              and the doctor's institution/location, not years of
              experience or qualifications, which live in Overview's
              Quick information and the Profile page below. */}
          <p className="muted">{[doctor.specialization, doctor.education_location].filter(Boolean).join(' · ')}</p>
          <span className={doctor.active ? 'pill status-active' : 'pill status-inactive'}>
            {doctor.active ? 'Active' : 'Inactive'}
          </span>
        </div>
        {isAdmin && (
          <div className="doctor-workspace-header-actions">
            <button type="button" className="btn-secondary btn btn-sm" onClick={() => guardedSetTab('profile')}>
              Edit profile
            </button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button type="button" className="icon-btn" aria-label="More doctor actions">
                  <DotsThree size={20} weight="bold" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem className="danger" onSelect={() => setDeactivateOpen(true)}>
                  Deactivate doctor
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </div>

      <AlertDialog open={deactivateOpen} onOpenChange={(open) => !open && setDeactivateOpen(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Deactivate {doctor.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes them from the Doctors directory, booking, and availability. Their schedule,
              appointments, and education history are kept and nothing is deleted, but there's currently
              no way to reactivate a doctor from this screen.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deactivateError && <p className="error">{deactivateError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>Keep active</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmDeactivate} disabled={deactivating}>
              {deactivating ? 'Deactivating…' : 'Deactivate'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <div className="tabs doctor-workspace-tabs">
        {PRIMARY_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={t.key === tab ? 'tab active' : 'tab'}
            onClick={() => guardedSetTab(t.key)}
          >
            {t.label}
          </button>
        ))}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={MORE_TABS.some((t) => t.key === tab) ? 'tab active doctor-workspace-more' : 'tab doctor-workspace-more'}
            >
              More <CaretDown size={13} weight="bold" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            {MORE_TABS.map((t) => (
              <DropdownMenuItem key={t.key} onSelect={() => guardedSetTab(t.key)}>
                {t.icon} {t.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {tab === 'overview' && (
        <OverviewSection
          doctor={doctor}
          onGoToTab={guardedSetTab}
          onGoToQueue={onGoToQueue ? () => onGoToQueue(doctor.id) : undefined}
        />
      )}
      {tab === 'appointments' && <DoctorAppointmentsTab doctor={doctor} isAdmin={isAdmin} />}
      {tab === 'schedule' && (
        <ScheduleSection doctor={doctor} isAdmin={isAdmin} onDirtyChange={setScheduleFormDirty} />
      )}
      {tab === 'blocks' && <BlocksSection doctor={doctor} />}
      {tab === 'departments' && <DepartmentAssignment doctor={doctor} isAdmin={isAdmin} />}
      {tab === 'types' && <AppointmentTypeAssignment doctor={doctor} isAdmin={isAdmin} />}
      {tab === 'profile' && <DoctorProfileSection doctor={doctor} isAdmin={isAdmin} />}
    </div>
  )
}

// -- Overview: "what is happening with this doctor today?" -------------

function OverviewSection({
  doctor,
  onGoToTab,
  onGoToQueue,
}: {
  doctor: Doctor
  onGoToTab: (tab: WorkspaceTab) => void
  onGoToQueue?: () => void
}) {
  const [appointments, setAppointments] = useState<AdminAppointment[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [appointmentTypes, setAppointmentTypes] = useState<AppointmentType[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  function load() {
    setLoading(true)
    setError(null)
    const today = isoDateToday()
    Promise.all([
      listAdminAppointments({ doctor_id: doctor.id, date_from: today, date_to: today }),
      getDoctorDepartments(doctor.id),
      listAppointmentTypesForDoctor(doctor.id),
    ])
      .then(([todaysAppointments, depts, types]) => {
        setAppointments([...todaysAppointments].sort((a, b) => a.start_at.localeCompare(b.start_at)))
        setDepartments(depts)
        setAppointmentTypes(types)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load today's overview"))
      .finally(() => setLoading(false))
  }

  useEffect(load, [doctor.id])

  if (loading) {
    return (
      <div className="state-block">
        <span className="spinner" aria-hidden="true" />
        Loading…
      </div>
    )
  }

  // A non-overlapping partition of every status an appointment can be
  // in (see app/services/appointment_services.py's status list), so
  // these four cards always sum to `appointments.length` -- unlike the
  // reference mockup's stat row, whose numbers didn't actually add up.
  const checkedIn = appointments.filter((a) => a.status === 'CHECKED_IN').length
  const completed = appointments.filter((a) => a.status === 'COMPLETED').length
  const cancelled = appointments.filter((a) => ['CANCELLED', 'REJECTED', 'NO_SHOW'].includes(a.status)).length
  const upcoming = appointments.filter((a) => a.status === 'PENDING' || a.status === 'CONFIRMED').length
  // eslint-disable-next-line react/purity -- read once per render, same as AppointmentsPanel's own "is this upcoming" check
  const now = Date.now()
  const next = appointments.find(
    (a) => (a.status === 'PENDING' || a.status === 'CONFIRMED') && new Date(a.start_at).getTime() >= now,
  )
  const summaryLine = doctorSummaryLine(doctor)
  const todaysSchedule = appointments.slice(0, 5)

  return (
    <div>
      {error && <p className="error">{error}</p>}

      <span className="overview-eyebrow">Today</span>
      <div className="dashboard-grid dashboard-grid-5" style={{ marginBottom: 'var(--space-5)' }}>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{appointments.length}</span>
            <span className="stat-label">Total appointments</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{checkedIn}</span>
            <span className="stat-label">Checked in</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{completed}</span>
            <span className="stat-label">Completed</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{upcoming}</span>
            <span className="stat-label">Upcoming</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{cancelled}</span>
            <span className="stat-label">Cancelled</span>
          </div>
        </div>
      </div>

      <div className="overview-columns">
        <div>
          <span className="overview-eyebrow">Next appointment</span>
          {next ? (
            <div className="next-appointment-card">
              <div>
                <strong>{formatTime(next.start_at)}</strong>
                <span className="next-appointment-patient">{next.patient_name}</span>
                <span className="muted">{next.appointment_type_name}</span>
                {next.token_number !== null && <span className="pill token-pill">Token #{next.token_number}</span>}
              </div>
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => onGoToTab('appointments')}>
                View appointment
              </button>
            </div>
          ) : (
            <p className="muted">No upcoming appointments today.</p>
          )}
        </div>

        <div>
          <span className="overview-eyebrow">Today's schedule</span>
          {todaysSchedule.length > 0 ? (
            <div className="today-schedule-list">
              {todaysSchedule.map((a) => (
                <div key={a.id} className="today-schedule-row">
                  <span className="today-schedule-time">{formatTime(a.start_at)}</span>
                  <span className="today-schedule-patient">{a.patient_name}</span>
                  <span className="muted">{a.appointment_type_name}</span>
                  <span className={`pill status-${a.status.toLowerCase()}`}>{a.status}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No appointments scheduled today.</p>
          )}
          <button type="button" className="link" onClick={() => onGoToTab('appointments')}>
            View full schedule →
          </button>
        </div>
      </div>

      <span className="overview-eyebrow">Quick information</span>
      <div className="quick-info-grid">
        <div>
          <span className="quick-info-label">Specialization</span>
          <span>{doctor.specialization ?? '—'}</span>
        </div>
        <div>
          <span className="quick-info-label">Experience</span>
          <span>{summaryLine ?? '—'}</span>
        </div>
        <div>
          <span className="quick-info-label">Departments</span>
          {departments.length > 0 ? (
            <span className="doctor-department-chips">
              {departments.map((d) => {
                const Icon = departmentIcon(d.name)
                return <DepartmentChip key={d.id} department={d} icon={<Icon size={12} weight="bold" />} />
              })}
            </span>
          ) : (
            <span className="muted">Not assigned to any department.</span>
          )}
        </div>
        <div>
          <span className="quick-info-label">Appointment types</span>
          <span>{appointmentTypes.length > 0 ? appointmentTypes.map((t) => t.name).join(' · ') : '—'}</span>
        </div>
      </div>

      <div className="doctor-quick-actions" style={{ marginTop: 'var(--space-5)' }}>
        <button type="button" className="btn-secondary btn btn-sm" onClick={() => onGoToTab('schedule')}>
          Manage schedule
        </button>
        <button type="button" className="btn-secondary btn btn-sm" onClick={() => onGoToTab('appointments')}>
          View appointments
        </button>
        <button type="button" className="btn-secondary btn btn-sm" onClick={() => onGoToTab('blocks')}>
          Manage time off
        </button>
        {onGoToQueue && (
          <button type="button" className="btn-secondary btn btn-sm" onClick={onGoToQueue}>
            View queue
          </button>
        )}
      </div>
    </div>
  )
}

// -- Appointments: "which patients are scheduled?" ----------------------
// Scoped to this doctor via the same admin appointments listing/action
// set AppointmentsPanel.tsx (the global Appointments page) already uses
// -- AppointmentActions.tsx's buildAppointmentActions and
// AppointmentDetailsModal are shared, not reimplemented, so a status's
// available actions are always computed the same one way.

function DoctorAppointmentsTab({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [appointments, setAppointments] = useState<AdminAppointment[]>([])
  // Today's snapshot for the stat row, independent of the table's own
  // when/status filters below -- so "8 total / 3 checked-in / ..." keeps
  // meaning "today" even while the admin is looking at, say, all-time
  // CANCELLED appointments in the table itself.
  const [todaysAppointments, setTodaysAppointments] = useState<AdminAppointment[]>([])
  const [when, setWhen] = useState<'today' | 'upcoming' | 'all'>('upcoming')
  const [statusFilter, setStatusFilter] = useState('')
  const [searchText, setSearchText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [lifecycleBusyId, setLifecycleBusyId] = useState<number | null>(null)
  const [cancelTarget, setCancelTarget] = useState<AdminAppointment | null>(null)
  const [reschedulingId, setReschedulingId] = useState<number | null>(null)
  const [rescheduleSlot, setRescheduleSlot] = useState<Slot | null>(null)
  const [rescheduleBusy, setRescheduleBusy] = useState(false)
  const [detailsTarget, setDetailsTarget] = useState<AdminAppointment | null>(null)

  function load() {
    setLoading(true)
    setError(null)
    listAdminAppointments({
      doctor_id: doctor.id,
      date_from: when === 'today' ? isoDateToday() : undefined,
      date_to: when === 'today' ? isoDateToday() : undefined,
      status: statusFilter || undefined,
    })
      .then((list) => setAppointments([...list].sort((a, b) => a.start_at.localeCompare(b.start_at))))
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointments'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [doctor.id, when, statusFilter])

  useEffect(() => {
    const today = isoDateToday()
    listAdminAppointments({ doctor_id: doctor.id, date_from: today, date_to: today })
      .then(setTodaysAppointments)
      .catch(() => undefined)
  }, [doctor.id])

  // Same non-overlapping status partition as Overview's stat row (see
  // that section's comment) -- always sums to todaysAppointments.length.
  const todayCheckedIn = todaysAppointments.filter((a) => a.status === 'CHECKED_IN').length
  const todayCompleted = todaysAppointments.filter((a) => a.status === 'COMPLETED').length
  const todayCancelled = todaysAppointments.filter((a) => ['CANCELLED', 'REJECTED', 'NO_SHOW'].includes(a.status)).length
  const todayUpcoming = todaysAppointments.filter((a) => a.status === 'PENDING' || a.status === 'CONFIRMED').length

  function exportCsv() {
    const header = ['Patient', 'Phone', 'Appointment type', 'Date', 'Time', 'Token', 'Status']
    const rows = visible.map((a) => [
      a.patient_name,
      a.whatsapp_number,
      a.appointment_type_name,
      formatDate(a.start_at),
      `${formatTime(a.start_at)} – ${formatTime(a.end_at)}`,
      a.token_number !== null ? String(a.token_number) : '',
      a.status,
    ])
    const csv = [header, ...rows]
      .map((row) => row.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${doctor.name.replace(/\s+/g, '_')}_appointments_${isoDateToday()}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  const searchNeedle = searchText.trim().toLowerCase()
  // eslint-disable-next-line react/purity -- read once per render, same convention as AppointmentsPanel.tsx
  const now = Date.now()
  const visible = appointments.filter((a) => {
    if (when === 'upcoming') {
      if (!['PENDING', 'CONFIRMED'].includes(a.status) || new Date(a.start_at).getTime() < now) return false
    }
    if (!searchNeedle) return true
    return a.patient_name.toLowerCase().includes(searchNeedle) || a.whatsapp_number.toLowerCase().includes(searchNeedle)
  })

  async function confirmCancel() {
    if (!cancelTarget) return
    setError(null)
    try {
      await cancelAdminAppointment(cancelTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not cancel the appointment')
    } finally {
      setCancelTarget(null)
    }
  }

  async function runLifecycleAction(
    appointmentId: number,
    action: (id: number) => Promise<{ id: number; status: string }>,
    failureMessage: string,
  ) {
    setError(null)
    setLifecycleBusyId(appointmentId)
    try {
      await action(appointmentId)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failureMessage)
    } finally {
      setLifecycleBusyId(null)
    }
  }

  function startReschedule(appointment: AdminAppointment) {
    setDetailsTarget(null)
    setReschedulingId((current) => (current === appointment.id ? null : appointment.id))
    setRescheduleSlot(null)
  }

  async function confirmReschedule(appointmentId: number) {
    if (!rescheduleSlot) return
    setError(null)
    setRescheduleBusy(true)
    try {
      await rescheduleAdminAppointment(appointmentId, rescheduleSlot.start_at)
      setReschedulingId(null)
      setRescheduleSlot(null)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reschedule the appointment')
    } finally {
      setRescheduleBusy(false)
    }
  }

  const reschedulingAppointment = appointments.find((a) => a.id === reschedulingId) ?? null

  const actionHandlers: AppointmentActionHandlers = {
    onConfirm: (a) => runLifecycleAction(a.id, confirmAdminAppointment, 'Could not confirm the appointment'),
    onReject: (a) => runLifecycleAction(a.id, rejectAdminAppointment, 'Could not reject the appointment'),
    onCheckIn: (a) => runLifecycleAction(a.id, visitAdminAppointment, 'Could not check in the appointment'),
    onMarkArrived: (a) => runLifecycleAction(a.id, markArrivedAdmin, 'Could not record the arrival'),
    onNoShow: (a) => runLifecycleAction(a.id, noShowAdminAppointment, 'Could not mark the appointment as a no-show'),
    onComplete: (a) => runLifecycleAction(a.id, completeAdminAppointment, 'Could not mark the appointment completed'),
    onReschedule: startReschedule,
    onCancel: (a) => {
      setDetailsTarget(null)
      setCancelTarget(a)
    },
    onViewDetails: setDetailsTarget,
    onCollectPayment: setDetailsTarget,
    onWaiveCharge: setDetailsTarget,
  }

  return (
    <div>
      {error && <p className="error">{error}</p>}

      <div className="dashboard-grid dashboard-grid-5" style={{ marginBottom: 'var(--space-5)' }}>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{todaysAppointments.length}</span>
            <span className="stat-label">Total today</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{todayCheckedIn}</span>
            <span className="stat-label">Checked in</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{todayCompleted}</span>
            <span className="stat-label">Completed</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{todayUpcoming}</span>
            <span className="stat-label">Upcoming</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-body">
            <span className="stat-value">{todayCancelled}</span>
            <span className="stat-label">Cancelled</span>
          </div>
        </div>
      </div>

      <div className="filter-bar">
        <div className="filter-bar-search-row">
          <div className="department-admin-search filter-bar-search">
            <input
              type="search"
              placeholder="Search patient name or number…"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />
          </div>
          <button type="button" className="btn-secondary btn btn-sm" onClick={exportCsv} disabled={visible.length === 0}>
            Export
          </button>
          <div className="filter-bar-fields">
            <label className="inline-label">
              When
              <Select value={when} onValueChange={(v) => setWhen(v as 'today' | 'upcoming' | 'all')}>
                <SelectTrigger className="filter-select-trigger">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="today">Today</SelectItem>
                  <SelectItem value="upcoming">Upcoming</SelectItem>
                  <SelectItem value="all">All</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className="inline-label">
              Status
              <Select
                value={statusFilter || ALL_FILTER_VALUE}
                onValueChange={(v) => setStatusFilter(v === ALL_FILTER_VALUE ? '' : v)}
              >
                <SelectTrigger className="filter-select-trigger">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_FILTER_VALUE}>All</SelectItem>
                  <SelectItem value="PENDING">Pending</SelectItem>
                  <SelectItem value="CONFIRMED">Confirmed</SelectItem>
                  <SelectItem value="REJECTED">Rejected</SelectItem>
                  <SelectItem value="CANCELLED">Cancelled</SelectItem>
                  <SelectItem value="CHECKED_IN">Checked in</SelectItem>
                  <SelectItem value="COMPLETED">Completed</SelectItem>
                  <SelectItem value="NO_SHOW">No-show</SelectItem>
                </SelectContent>
              </Select>
            </label>
          </div>
        </div>
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}
      {!loading && visible.length === 0 && (
        <p className="muted">{appointments.length === 0 ? 'No appointments found.' : 'No appointments match your search.'}</p>
      )}

      {!loading && visible.length > 0 && (
        <div className="admin-table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Patient</th>
              <th>Type</th>
              <th>Date</th>
              <th>Time</th>
              <th>Queue / Token</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((a) => (
              <Fragment key={a.id}>
                <tr>
                  <td>
                    {a.patient_name}
                    <div className="muted">{a.whatsapp_number}</div>
                  </td>
                  <td>{a.appointment_type_name}</td>
                  <td>{formatDate(a.start_at)}</td>
                  <td>
                    {formatTime(a.start_at)} – {formatTime(a.end_at)}
                  </td>
                  <td>{a.token_number !== null ? `#${a.token_number}` : <span className="muted">—</span>}</td>
                  <td>
                    {(() => {
                      const arrival = describeArrival(a)
                      return arrival ? (
                        <span className={arrival.className} title={arrival.sub}>
                          {arrival.label}
                        </span>
                      ) : (
                        <span className={`pill status-${a.status.toLowerCase()}`}>{a.status.replace(/_/g, ' ')}</span>
                      )
                    })()}
                  </td>
                  <td>
                    <AppointmentActionButtons
                      actions={buildAppointmentActions(a, actionHandlers, isAdmin)}
                      busy={lifecycleBusyId === a.id}
                    />
                  </td>
                </tr>
                {reschedulingId === a.id && reschedulingAppointment && (
                  <tr>
                    <td colSpan={7}>
                      <div className="detail-section appointment-reschedule-panel">
                        <h4>Reschedule {reschedulingAppointment.patient_name}</h4>
                        <p className="muted">
                          Currently {formatDate(reschedulingAppointment.start_at)} · {formatTime(reschedulingAppointment.start_at)} –{' '}
                          {formatTime(reschedulingAppointment.end_at)}
                        </p>
                        <AdminSlotPicker
                          doctorId={reschedulingAppointment.doctor_id}
                          appointmentTypeId={reschedulingAppointment.appointment_type_id}
                          durationMinutes={Math.round(
                            (new Date(reschedulingAppointment.end_at).getTime() -
                              new Date(reschedulingAppointment.start_at).getTime()) /
                              60000,
                          )}
                          selectedSlot={rescheduleSlot}
                          onSelect={setRescheduleSlot}
                        />
                        <div className="appointment-reschedule-actions">
                          <button
                            type="button"
                            className="btn btn-sm"
                            disabled={!rescheduleSlot || rescheduleBusy}
                            onClick={() => confirmReschedule(a.id)}
                          >
                            {rescheduleBusy ? 'Saving…' : 'Confirm new time'}
                          </button>
                          <button type="button" className="btn-secondary btn btn-sm" onClick={() => setReschedulingId(null)}>
                            Cancel
                          </button>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        </div>
      )}

      {detailsTarget && (
        <AppointmentDetailsModal
          appointment={detailsTarget}
          onClose={() => setDetailsTarget(null)}
          handlers={actionHandlers}
          busy={lifecycleBusyId === detailsTarget.id}
          isAdmin={isAdmin}
          onPaymentUpdated={load}
        />
      )}

      <AlertDialog open={cancelTarget !== null} onOpenChange={(open) => !open && setCancelTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel this appointment?</AlertDialogTitle>
            <AlertDialogDescription>
              {cancelTarget && `Cancel ${cancelTarget.patient_name}'s appointment on ${formatDate(cancelTarget.start_at)}?`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmCancel}>
              Cancel appointment
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// -- Department assignment (ADMIN only) ---------------------------------

function DepartmentAssignment({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [assigned, setAssigned] = useState<DoctorDepartmentAssignment[]>([])
  const [allDepartments, setAllDepartments] = useState<Department[]>([])
  const [selected, setSelected] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [removeTarget, setRemoveTarget] = useState<Department | null>(null)

  function load() {
    Promise.all([getDoctorDepartments(doctor.id), listDepartments()])
      .then(([assignedList, all]) => {
        setAssigned(assignedList)
        setAllDepartments(all)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load departments'))
  }

  useEffect(load, [doctor.id])

  const unassigned = allDepartments.filter((d) => !assigned.some((a) => a.id === d.id))
  // The earliest-assigned department is shown as "Primary" -- see
  // DepartmentChip's own `primary` prop docstring for why this is
  // derived rather than a stored flag.
  const earliestAssignedAt =
    assigned.length > 0 ? assigned.reduce((min, d) => (d.assigned_at < min ? d.assigned_at : min), assigned[0].assigned_at) : null

  async function handleAssign(e: React.FormEvent) {
    e.preventDefault()
    if (!selected) return
    setError(null)
    try {
      await assignDoctorToDepartment(doctor.id, Number(selected))
      setSelected('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not assign department')
    }
  }

  async function confirmRemove() {
    if (!removeTarget) return
    setError(null)
    try {
      await removeDoctorFromDepartment(doctor.id, removeTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove department')
    } finally {
      setRemoveTarget(null)
    }
  }

  return (
    <div>
      <h4>Departments</h4>
      <p className="muted">
        This doctor currently works in the departments below. Department-specific working hours (Schedule tab) only
        apply once a doctor is assigned here.
      </p>
      {error && <p className="error">{error}</p>}
      {assigned.length > 0 ? (
        <span className="doctor-department-chips">
          {assigned.map((d) => {
            const Icon = departmentIcon(d.name)
            const isPrimary = d.assigned_at === earliestAssignedAt
            return (
              <DepartmentChip
                key={d.id}
                department={d}
                icon={<Icon size={12} weight="bold" />}
                onRemove={isAdmin ? () => setRemoveTarget(d) : undefined}
                primary={isPrimary}
              />
            )
          })}
        </span>
      ) : (
        <p className="muted">Not assigned to any department.</p>
      )}
      {isAdmin && unassigned.length > 0 && (
        <form className="inline-form wrap" onSubmit={handleAssign}>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} required>
            <option value="">Add department…</option>
            {unassigned.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <button type="submit">+ Add department</button>
          {selected && (
            <button type="button" className="link" onClick={() => setSelected('')}>
              Cancel
            </button>
          )}
        </form>
      )}

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove department?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget && `Remove ${doctor.name} from ${removeTarget.name}?`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// -- Recurring weekly schedule (ADMIN only, WEB P7 date ranges) ---------

// Does `dayOfWeek` (1=Monday..7=Sunday) occur at least once between
// startDate and endDate (inclusive)? A schedule row bounded to a date
// range that never actually contains its own day-of-week can be created
// today with no error and then silently never produce a single bookable
// day -- e.g. "Monday, 10 Sep - 11 Sep" when the 10th and 11th are a
// Wednesday and Thursday. An open-ended side (no start or no end) always
// eventually reaches every weekday, so only a fully-bounded range needs
// checking.
function dayOfWeekOccursInRange(dayOfWeek: number, startDate: string, endDate: string): boolean {
  const start = new Date(`${startDate}T00:00:00`)
  const end = new Date(`${endDate}T00:00:00`)
  if (start > end) return true // a different problem (end before start) -- not this check's job
  const jsTargetDay = dayOfWeek % 7 // Date.getDay(): Sunday=0..Saturday=6; ours: Monday=1..Sunday=7
  const cursor = new Date(start)
  while (cursor <= end) {
    if (cursor.getDay() === jsTargetDay) return true
    cursor.setDate(cursor.getDate() + 1)
  }
  return false
}

// Every "appointment duration"-shaped <select> on this tab must offer
// the exact same choices, since defaultDuration is shared as a single
// piece of draft state across both the Slot Settings card and the
// preview generation below.
const DURATION_OPTIONS = [15, 20, 30, 45, 60, 90]

// One synthetic doctor_schedule-shaped row per (day x break segment) of
// the working-hours form currently being composed -- fake, negative
// ids so they can never collide with a real persisted entry's id. Used
// only to feed the *same* generateDaySlots() the sidebar preview always
// used, alongside the real persisted `entries`, so the preview can show
// "what this day will look like after Save" without a second
// slot-generation implementation (see ScheduleSection's own preview
// wiring below for why this union, not a replacement, is the point).
function draftEntriesFor(
  selectedDays: number[],
  segments: { start: string; end: string }[],
  startDate: string,
  endDate: string,
  departmentId: string,
): DoctorScheduleEntry[] {
  let fakeId = -1
  return selectedDays.flatMap((day) =>
    segments.map((seg) => ({
      id: fakeId--,
      day_of_week: day,
      start_time: seg.start,
      end_time: seg.end,
      active: true,
      start_date: startDate || null,
      end_date: endDate || null,
      department_id: departmentId ? Number(departmentId) : null,
    })),
  )
}

function ScheduleSection({
  doctor,
  isAdmin,
  onDirtyChange,
}: {
  doctor: Doctor
  isAdmin: boolean
  onDirtyChange?: (dirty: boolean) => void
}) {
  const [entries, setEntries] = useState<DoctorScheduleEntry[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  // Multiple days at once (e.g. Mon-Fri) rather than one day per submit
  // -- each still becomes its own doctor_schedule row server-side (see
  // saveScheduleRows below), the backend has no multi-day concept, this
  // form just saves the admin from resubmitting the same hours/date
  // range/department N times for N days.
  const [selectedDays, setSelectedDays] = useState<number[]>([1])
  const [startTime, setStartTime] = useState('09:00')
  const [endTime, setEndTime] = useState('17:00')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [departmentId, setDepartmentId] = useState('')
  const [breaks, setBreaks] = useState<{ start: string; end: string }[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [savedFlash, setSavedFlash] = useState(false)
  const [removeTarget, setRemoveTarget] = useState<DoctorScheduleEntry | null>(null)

  // "Copy from another week" (spec decision: this schedule is a
  // recurring day-of-week pattern, not per-calendar-week, so "copy"
  // means copying one day's working hours onto other days).
  const [showCopyForm, setShowCopyForm] = useState(false)
  const [copySourceDay, setCopySourceDay] = useState('')
  const [copyTargetDays, setCopyTargetDays] = useState<number[]>([])
  const [copyBusy, setCopyBusy] = useState(false)
  const [copyError, setCopyError] = useState<string | null>(null)

  // Slot settings (migrations/0022_doctor_slot_settings.sql) --
  // defaultDuration/bufferMinutes are DRAFT values now, part of the one
  // Working Hours form's save/cancel lifecycle (no independent "Save
  // settings" button -- see the single footer below). savedDuration/
  // savedBuffer track what's actually persisted, so slotSettingsDirty
  // and Cancel's revert both have a real "last saved" baseline to
  // compare/reset against, one that updates after a real successful
  // save even if it happens as part of a partially-failed combined
  // save (see handleSaveAll).
  const [defaultDuration, setDefaultDuration] = useState(doctor.default_duration_minutes)
  const [bufferMinutes, setBufferMinutes] = useState(doctor.buffer_minutes)
  const [savedDuration, setSavedDuration] = useState(doctor.default_duration_minutes)
  const [savedBuffer, setSavedBuffer] = useState(doctor.buffer_minutes)
  const slotSettingsDirty = defaultDuration !== savedDuration || bufferMinutes !== savedBuffer

  // Generated slots preview panel -- a generic, capacity-only preview
  // for a chosen date (see generateDaySlots's own docstring for why
  // this isn't the same thing as real per-appointment-type booking
  // availability). Always visible now (no show/hide toggle) -- it's
  // the single preview for both the in-progress draft and the
  // last-saved schedule, see the combinedEntries wiring below.
  const [previewDate, setPreviewDate] = useState(isoDateToday())

  function load() {
    // The doctor's own assigned departments (not every department in the
    // system) populate the schedule form's department picker -- a row
    // can only be scoped to a department this doctor actually belongs
    // to, matching what app/api/doctor_schedule.py's create/update
    // handlers validate server-side.
    Promise.all([getDoctorScheduleAdmin(doctor.id), getDoctorDepartments(doctor.id)])
      .then(([schedule, depts]) => {
        setEntries(schedule)
        setDepartments(depts)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load schedule'))
  }

  useEffect(load, [doctor.id])

  function departmentName(id: number | null): string {
    if (id === null) return 'All departments'
    return departments.find((d) => d.id === id)?.name ?? 'All departments'
  }

  function toggleDay(day: number) {
    setSelectedDays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort((a, b) => a - b),
    )
  }

  function addBreak() {
    setBreaks((prev) => [...prev, { start: '13:00', end: '14:00' }])
  }

  function updateBreak(index: number, field: 'start' | 'end', value: string) {
    setBreaks((prev) => prev.map((b, i) => (i === index ? { ...b, [field]: value } : b)))
  }

  function removeBreak(index: number) {
    setBreaks((prev) => prev.filter((_, i) => i !== index))
  }

  const sortedBreaks = [...breaks].sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0))

  // There is no "break" field on the backend -- a doctor_schedule row is
  // just one contiguous start_time/end_time range (see
  // app/api/doctor_schedule.py). Any number of breaks is expressed the
  // same way the availability engine already supports it: separate,
  // non-overlapping rows for the same day (app/services/
  // availability_engine.py iterates every matching row and generates
  // slots per-row, so a gap between rows naturally has no slots in it).
  // This form just saves the admin from having to work that split out
  // by hand and submit it as several manual "Add schedule" round trips.
  function validateBreaks(): string | null {
    for (const b of sortedBreaks) {
      if (!(b.start < b.end)) return "Each break's end time must be after its start time"
      if (!(startTime < b.start) || !(b.end < endTime)) {
        return 'Breaks must fall entirely within the working hours'
      }
    }
    for (let i = 1; i < sortedBreaks.length; i++) {
      if (sortedBreaks[i].start < sortedBreaks[i - 1].end) {
        return 'Breaks cannot overlap each other'
      }
    }
    return null
  }

  // The working hours split around zero or more sorted, non-overlapping
  // breaks -- e.g. 09:00-17:00 with breaks at 11:00-11:15 and
  // 13:00-14:00 becomes [09:00-11:00, 11:15-13:00, 14:00-17:00]. With no
  // breaks this is just the one original [startTime, endTime] segment.
  function scheduleSegments(): { start: string; end: string }[] {
    const segments: { start: string; end: string }[] = []
    let cursor = startTime
    for (const b of sortedBreaks) {
      segments.push({ start: cursor, end: b.start })
      cursor = b.end
    }
    segments.push({ start: cursor, end: endTime })
    return segments
  }

  // Resets the working-hours draft fields back to their defaults --
  // handleCancelAll (the one footer's "Cancel") also resets Slot
  // Settings, since both are part of the same save/cancel lifecycle now.
  function resetForm() {
    setSelectedDays([1])
    setStartTime('09:00')
    setEndTime('17:00')
    setStartDate('')
    setEndDate('')
    setDepartmentId('')
    setBreaks([])
  }

  const formIsDirty =
    selectedDays.length !== 1 ||
    selectedDays[0] !== 1 ||
    startTime !== '09:00' ||
    endTime !== '17:00' ||
    startDate !== '' ||
    endDate !== '' ||
    departmentId !== '' ||
    breaks.length > 0

  // The one dirty flag for the whole card -- Working Hours draft fields
  // OR Slot Settings values that differ from what's actually persisted.
  const overallDirty = formIsDirty || slotSettingsDirty

  // Surfaced to the parent DoctorWorkspace so switching tabs or going
  // back to the Doctors directory can warn before discarding an
  // in-progress (not yet saved) edit -- see DoctorWorkspace's own
  // guardedSetTab/guardedOnBack.
  useEffect(() => {
    onDirtyChange?.(overallDirty)
    return () => onDirtyChange?.(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overallDirty])

  // At least one day, and a real (non-empty) start-before-end range --
  // Department is deliberately NOT required here: "All departments" is
  // itself a valid, common choice (see the field's own helper text),
  // not an incomplete one.
  const scheduleValid = selectedDays.length > 0 && startTime < endTime
  const saveDisabled = busy || !overallDirty || (formIsDirty && !scheduleValid)
  const cancelDisabled = busy || !overallDirty

  // Submits the working-hours draft as N separate doctor_schedule rows
  // (one per selected-day x break-segment) -- there is no batch/
  // transaction endpoint (see app/api/doctor_schedule.py), so this
  // stays N sequential calls exactly as before, just no longer its own
  // independently-clickable action (see handleSaveAll below). On a
  // partial failure, narrows selectedDays down to only the day(s) not
  // yet confirmed saved (successful days are dropped from the draft --
  // they're already reflected in `entries` after load()) rather than
  // clearing or leaving the whole original selection dirty, so
  // "Unsaved changes" continues to describe exactly what's left to
  // retry, not what already succeeded.
  async function saveScheduleRows(): Promise<boolean> {
    if (startDate && endDate) {
      const daysNeverInRange = selectedDays.filter((d) => !dayOfWeekOccursInRange(d, startDate, endDate))
      if (daysNeverInRange.length > 0) {
        const names = daysNeverInRange.map((d) => DAY_NAMES[d]).join(', ')
        setError(
          `${names} ${daysNeverInRange.length === 1 ? "doesn't" : "don't"} fall between ${formatDate(startDate)} and ` +
            `${formatDate(endDate)}, so ${daysNeverInRange.length === 1 ? 'that schedule' : 'those schedules'} would ` +
            'never actually apply. Pick a date range that includes at least one of each selected day, or deselect it.',
        )
        return false
      }
    }

    const breaksError = validateBreaks()
    if (breaksError) {
      setError(breaksError)
      return false
    }

    const segments = scheduleSegments()
    const succeededDays: number[] = []
    for (const day of selectedDays) {
      try {
        for (const seg of segments) {
          await createDoctorSchedule(doctor.id, {
            day_of_week: day,
            start_time: seg.start,
            end_time: seg.end,
            start_date: startDate || null,
            end_date: endDate || null,
            department_id: departmentId ? Number(departmentId) : null,
          })
        }
        succeededDays.push(day)
      } catch (err) {
        setSelectedDays(selectedDays.filter((d) => !succeededDays.includes(d)))
        setError(
          `Saved working hours for ${succeededDays.length} of ${selectedDays.length} day(s), but could not save ` +
            `${DAY_NAMES[day]}: ${err instanceof ApiError ? err.message : 'unknown error'}`,
        )
        return false
      }
    }
    setStartDate('')
    setEndDate('')
    return true
  }

  // The one Save action for the whole card: Slot Settings (if changed)
  // first, then the working-hours draft (if any) -- see the PLAN's own
  // reasoning for that order (settings is a single atomic PATCH; if it
  // fails, stop before attempting the schedule rows at all, rather than
  // leaving the two saved states out of sync in a confusing order).
  async function handleSaveAll(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (slotSettingsDirty) {
        try {
          await updateDoctorSlotSettings(doctor.id, {
            default_duration_minutes: defaultDuration,
            buffer_minutes: bufferMinutes,
          })
          setSavedDuration(defaultDuration)
          setSavedBuffer(bufferMinutes)
        } catch (err) {
          setError(err instanceof ApiError ? err.message : 'Could not save slot settings')
          return
        }
      }
      if (formIsDirty) {
        const scheduleSaved = await saveScheduleRows()
        if (!scheduleSaved) return
      }
      load()
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 2500)
    } finally {
      setBusy(false)
    }
  }

  function handleCancelAll() {
    resetForm()
    setDefaultDuration(savedDuration)
    setBufferMinutes(savedBuffer)
    setError(null)
  }

  async function confirmRemove() {
    if (!removeTarget) return
    setError(null)
    try {
      await deleteDoctorSchedule(doctor.id, removeTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove schedule')
    } finally {
      setRemoveTarget(null)
    }
  }

  const daysWithEntries = [...new Set(entries.map((e) => e.day_of_week))].sort((a, b) => a - b)

  function toggleCopyTargetDay(day: number) {
    setCopyTargetDays((prev) => (prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort((a, b) => a - b)))
  }

  async function handleCopyHours(e: React.FormEvent) {
    e.preventDefault()
    setCopyError(null)
    if (!copySourceDay) {
      setCopyError('Pick a day to copy hours from.')
      return
    }
    const sourceDay = Number(copySourceDay)
    const sourceRows = entries.filter((entry) => entry.day_of_week === sourceDay)
    if (sourceRows.length === 0 || copyTargetDays.length === 0) {
      setCopyError('Pick a source day with hours and at least one target day.')
      return
    }
    setCopyBusy(true)
    let copied = 0
    try {
      for (const targetDay of copyTargetDays) {
        for (const row of sourceRows) {
          try {
            await createDoctorSchedule(doctor.id, {
              day_of_week: targetDay,
              start_time: row.start_time,
              end_time: row.end_time,
              start_date: row.start_date,
              end_date: row.end_date,
              department_id: row.department_id,
            })
            copied++
          } catch {
            // Most likely an overlap with hours the target day already
            // has -- skip it and keep going with the rest rather than
            // aborting the whole copy over one conflicting day.
          }
        }
      }
      setShowCopyForm(false)
      setCopySourceDay('')
      setCopyTargetDays([])
      load()
      if (copied === 0) {
        setCopyError('Nothing was copied -- the target days already have overlapping hours.')
      }
    } finally {
      setCopyBusy(false)
    }
  }

  // The single preview: while the working-hours draft is dirty, union
  // the real persisted `entries` with a synthetic entry for the day(s)
  // currently being composed, so the preview shows what the day will
  // actually look like after Save -- not just the new block in
  // isolation (which would just reintroduce "preview doesn't match
  // reality" one level down). Not deduplicated against an overlapping
  // existing row -- schedule_overlaps() on the backend is still what
  // actually rejects that at Save time; this preview only visualizes,
  // it never validates.
  const draftEntries = formIsDirty
    ? draftEntriesFor(selectedDays, scheduleSegments(), startDate, endDate, departmentId)
    : []
  const combinedEntries = formIsDirty ? [...entries, ...draftEntries] : entries
  const previewDayOfWeek = dateToDayOfWeek(previewDate)
  const previewSlotsForDate = generateDaySlots(combinedEntries, previewDate, previewDayOfWeek, defaultDuration, bufferMinutes)

  return (
    <div>
      <div className="admin-content-header">
        <div>
          <h4 style={{ margin: 0 }}>Working hours</h4>
          <p className="muted" style={{ margin: 0 }}>Set when this doctor is available for appointments.</p>
        </div>
        {isAdmin && daysWithEntries.length > 0 && (
          <button type="button" className="btn-secondary btn btn-sm" onClick={() => setShowCopyForm((v) => !v)}>
            {showCopyForm ? 'Cancel' : 'Copy hours across days'}
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}

      {showCopyForm && (
        <form className="inline-form wrap" onSubmit={handleCopyHours}>
          <label className="inline-label">
            Copy hours from
            <select value={copySourceDay} onChange={(e) => setCopySourceDay(e.target.value)} required>
              <option value="">Choose a day…</option>
              {daysWithEntries.map((d) => (
                <option key={d} value={d}>
                  {DAY_NAMES[d]}
                </option>
              ))}
            </select>
          </label>
          <div style={{ width: '100%' }}>
            <span className="field-label">To</span>
            <div className="day-multiselect" role="group" aria-label="Target days">
              {DAY_NAMES.slice(1).map((name, i) => {
                const day = i + 1
                const isSource = copySourceDay !== '' && Number(copySourceDay) === day
                return (
                  <button
                    key={day}
                    type="button"
                    className={copyTargetDays.includes(day) ? 'selected' : ''}
                    aria-pressed={copyTargetDays.includes(day)}
                    disabled={isSource}
                    onClick={() => toggleCopyTargetDay(day)}
                  >
                    {name.slice(0, 3)}
                  </button>
                )
              })}
            </div>
          </div>
          {copyError && <p className="error">{copyError}</p>}
          <button type="submit" disabled={copyBusy}>
            {copyBusy ? 'Copying…' : 'Copy hours'}
          </button>
        </form>
      )}

      <div className="admin-table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Day</th>
            <th>Hours</th>
            <th>Date range</th>
            <th>Department</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td>{DAY_NAMES[e.day_of_week]}</td>
              <td>
                {formatTimeOfDay(e.start_time)} – {formatTimeOfDay(e.end_time)}
              </td>
              <td>
                {e.start_date || e.end_date
                  ? `${e.start_date ? formatDate(e.start_date) : 'Always'} – ${e.end_date ? formatDate(e.end_date) : 'Always'}`
                  : 'Every week'}
              </td>
              <td>{departmentName(e.department_id)}</td>
              <td>
                {isAdmin && (
                  <button type="button" className="link" onClick={() => setRemoveTarget(e)}>
                    remove
                  </button>
                )}
              </td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                No working hours set.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>

      {/* Removing a saved row stays its own, immediately-persisted
          action (own confirm dialog, own DELETE call) -- deliberately
          NOT folded into the pending Save/Cancel diff below, which is
          scoped to composing a new working-hours entry plus Slot
          Settings. Undoing a queued delete would need real "undo"
          machinery this screen has no other use for. */}
      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this schedule?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget &&
                `Remove the ${DAY_NAMES[removeTarget.day_of_week]} ${formatTimeOfDay(removeTarget.start_time)}–${formatTimeOfDay(removeTarget.end_time)} schedule? Patients will no longer be able to book into this slot.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <div className={overallDirty ? 'schedule-tab-layout is-dirty' : 'schedule-tab-layout'}>
        <div className="schedule-tab-main">
          {isAdmin && (
            <form id="working-hours-form" onSubmit={handleSaveAll}>
              <div className="schedule-field-group">
                <span className="field-label">Days</span>
                <div className="day-multiselect" role="group" aria-label="Days of week">
                  {DAY_NAMES.slice(1).map((name, i) => {
                    const day = i + 1
                    return (
                      <button
                        key={day}
                        type="button"
                        className={selectedDays.includes(day) ? 'selected' : ''}
                        aria-pressed={selectedDays.includes(day)}
                        onClick={() => toggleDay(day)}
                      >
                        {name.slice(0, 3)}
                      </button>
                    )
                  })}
                </div>
              </div>

              <div className="schedule-field-group">
                <span className="field-label">Hours</span>
                <div className="schedule-field-grid">
                  <label className="schedule-field-group">
                    <span className="field-label">From</span>
                    <TimeCombobox value={startTime} onChange={setStartTime} durationMinutes={defaultDuration} ariaLabel="Start time" />
                  </label>
                  <label className="schedule-field-group">
                    <span className="field-label">Until</span>
                    <TimeCombobox value={endTime} onChange={setEndTime} durationMinutes={defaultDuration} ariaLabel="End time" />
                  </label>
                </div>
              </div>

              <div className="schedule-field-group">
                <span className="field-label">Date range</span>
                <div className="schedule-field-grid">
                  {/* A plain div, not <label> -- AdminDatePicker is a
                      compound widget with its own toggle button AND a
                      calendar full of day buttons, not a single native
                      form control. A <label> wrapping it delegates a
                      click anywhere inside (including a day cell deep
                      in the calendar) to its first focusable
                      descendant, re-firing a *second*, browser-native
                      click on the toggle button right after a day
                      pick's own setOpen(false) -- reopening the panel
                      it had just correctly closed. Confirmed via a
                      real (non-synthetic, isTrusted) click event
                      landing on the toggle button immediately after
                      picking a date; BlocksSection's own
                      AdminDatePicker (never wrapped in a <label>)
                      never had this problem. */}
                  <div className="schedule-field-group">
                    <span className="field-label">Start date</span>
                    <AdminDatePicker value={startDate} onChange={setStartDate} label="Pick start date" />
                  </div>
                  <div className="schedule-field-group">
                    <span className="field-label">End date</span>
                    <AdminDatePicker value={endDate} onChange={setEndDate} label="Pick end date" />
                  </div>
                </div>
              </div>

              <label className="schedule-field-group">
                <span className="field-label">Department</span>
                <select
                  value={departmentId}
                  onChange={(e) => setDepartmentId(e.target.value)}
                  disabled={departments.length === 0}
                >
                  <option value="">All departments</option>
                  {departments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
                {departments.length === 0 && (
                  <span className="muted">Assign a department (Departments tab) to set department-specific hours.</span>
                )}
              </label>

              <div className="schedule-field-group schedule-breaks">
                <span className="field-label">Breaks</span>
                {breaks.map((b, i) => (
                  <div key={i} className="schedule-break-row">
                    <TimeCombobox
                      value={b.start}
                      onChange={(v) => updateBreak(i, 'start', v)}
                      durationMinutes={defaultDuration}
                      ariaLabel={`Break ${i + 1} start`}
                    />
                    <span className="arrow">→</span>
                    <TimeCombobox
                      value={b.end}
                      onChange={(v) => updateBreak(i, 'end', v)}
                      durationMinutes={defaultDuration}
                      ariaLabel={`Break ${i + 1} end`}
                    />
                    <button type="button" className="link danger" onClick={() => removeBreak(i)}>
                      Remove
                    </button>
                  </div>
                ))}
                <button type="button" className="link" onClick={addBreak}>
                  + Add a break
                </button>
              </div>
            </form>
          )}
        </div>

        <div className="schedule-tab-sidebar">
          {isAdmin && (
            <div className="schedule-sidebar-panel">
              <h4 style={{ margin: 0 }}>Slot settings</h4>
              <label className="inline-label">
                Default appointment duration
                <select value={defaultDuration} onChange={(e) => setDefaultDuration(Number(e.target.value))}>
                  {DURATION_OPTIONS.map((d) => (
                    <option key={d} value={d}>
                      {d} minutes
                    </option>
                  ))}
                </select>
              </label>
              {/* This value is NOT what real patient-facing booking
                  slots use -- that's per doctor+appointment type
                  (doctor_appointment_types.duration_minutes, edited on
                  the Appointment Types tab). This field only feeds the
                  preview below and is deliberately a separate,
                  doctor-level default; stated plainly here so an admin
                  never reasonably assumes changing it changes what
                  patients can book. */}
              <p className="muted schedule-sidebar-note">
                Used for this preview only -- actual appointment lengths are set per appointment type (Appointment
                Types tab).
              </p>
              <label className="inline-label">
                Buffer time between appointments
                <select value={bufferMinutes} onChange={(e) => setBufferMinutes(Number(e.target.value))}>
                  {[0, 5, 10, 15, 20, 30].map((b) => (
                    <option key={b} value={b}>
                      {b === 0 ? 'No buffer' : `${b} minutes`}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          <div className="schedule-sidebar-panel">
            <div className="schedule-preview-heading">
              <h4 style={{ margin: 0 }}>Generated slots preview</h4>
              {/* The one preview for both an in-progress draft and the
                  last-saved schedule (see combinedEntries above) --
                  labeled whenever there's anything unsaved so it's
                  never mistaken for what's actually live/bookable
                  right now. */}
              {overallDirty && <span className="draft-badge">Previewing unsaved changes</span>}
            </div>
            <p className="muted" style={{ margin: 0 }}>Preview available slots for a selected date.</p>
            <AdminDatePicker value={previewDate} onChange={setPreviewDate} label="Pick a date" />
            <p className="muted" style={{ margin: 0 }}>Available slots ({previewSlotsForDate.length})</p>
            {previewSlotsForDate.length > 0 ? (
              <div className="schedule-preview-slots">
                {previewSlotsForDate.map((s, i) => (
                  <span key={i} className="slot-chip-static">
                    {s}
                  </span>
                ))}
              </div>
            ) : (
              <p className="muted">No working hours set for this day.</p>
            )}
            <p className="muted schedule-sidebar-note">
              Slots are generated from working hours, appointment duration, and buffer time -- not a guarantee of real
              booking availability (existing appointments and time off aren't excluded here).
            </p>
          </div>
        </div>
      </div>

      {isAdmin && (
        <div className="schedule-save-bar">
          {/* savedFlash takes priority over overallDirty for its brief
              window -- after a successful save, the draft fields stay
              populated (so a quick repeat-add doesn't require retyping
              hours; see saveScheduleRows' own docstring), which would
              otherwise make this say "Unsaved changes" again the
              instant a real save just succeeded. */}
          <span className={savedFlash ? 'schedule-save-status success' : 'schedule-save-status'}>
            {savedFlash ? '✓ Saved' : overallDirty ? 'Unsaved changes' : ''}
          </span>
          <div className="schedule-save-bar-actions">
            <button type="button" className="btn-secondary btn" onClick={handleCancelAll} disabled={cancelDisabled}>
              Cancel
            </button>
            <button type="submit" form="working-hours-form" className="btn" disabled={saveDisabled}>
              {busy ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// -- Time off: one-off blocks (ADMIN or STAFF) --------------------------
// Entered/shown in Asia/Kolkata terms -- see types.ts's DoctorBlockEntry
// docstring for why (no API currently exposes/sets a doctor's timezone,
// and every doctor created through this admin UI is Asia/Kolkata by the
// doctors table's own column default).

function BlocksSection({ doctor }: { doctor: Doctor }) {
  const [blocks, setBlocks] = useState<DoctorBlockEntry[]>([])
  const [date, setDate] = useState('')
  const [startTime, setStartTime] = useState('09:00')
  const [endTime, setEndTime] = useState('10:00')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [removeTarget, setRemoveTarget] = useState<DoctorBlockEntry | null>(null)

  function load() {
    getDoctorBlocks(doctor.id)
      .then(setBlocks)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load time off'))
  }

  useEffect(load, [doctor.id])

  function resetForm() {
    setDate('')
    setStartTime('09:00')
    setEndTime('10:00')
    setReason('')
    setShowForm(false)
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    // The date field is no longer a native <input required> (it's the
    // calendar-panel AdminDatePicker below, which has no built-in HTML
    // validation), so this guard replaces what `required` used to do.
    if (!date) {
      setError('Choose a date for this time off')
      return
    }
    setBusy(true)
    try {
      await createDoctorBlock(doctor.id, `${date}T${startTime}:00+05:30`, `${date}T${endTime}:00+05:30`, reason)
      resetForm()
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add time off')
    } finally {
      setBusy(false)
    }
  }

  async function confirmRemove() {
    if (!removeTarget) return
    setError(null)
    try {
      await deleteDoctorBlock(doctor.id, removeTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove time off')
    } finally {
      setRemoveTarget(null)
    }
  }

  return (
    <div>
      <div className="admin-content-header">
        <div>
          <h4 style={{ margin: 0 }}>Time off</h4>
          <p className="muted" style={{ margin: 0 }}>
            One-off exceptions to the regular working hours (IST) -- these block appointment slots without touching
            the recurring schedule.
          </p>
        </div>
        <button type="button" className="btn btn-sm" onClick={() => setShowForm((v) => !v)}>
          {showForm ? 'Cancel' : '+ Add time off'}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      <ul className="tag-list">
        {blocks.map((b) => (
          <li key={b.id}>
            {formatDate(b.start_at)} {formatTime(b.start_at)} – {formatTime(b.end_at)}: {b.reason}
            <span className="muted"> · Added {formatDate(b.created_at)}</span>
            <button type="button" className="link" onClick={() => setRemoveTarget(b)}>
              remove
            </button>
          </li>
        ))}
        {blocks.length === 0 && <li className="muted">No time off scheduled.</li>}
      </ul>

      {showForm && (
        <form className="inline-form wrap" onSubmit={handleCreate}>
          <AdminDatePicker value={date} onChange={setDate} label={date ? 'Change date' : 'Pick a date'} />
          <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} required />
          <input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} required />
          <input placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} required />
          <button type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </button>
          <button type="button" className="btn-secondary btn btn-sm" onClick={resetForm}>
            Cancel
          </button>
        </form>
      )}

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this time off?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget &&
                `Remove the ${formatDate(removeTarget.start_at)} ${formatTime(removeTarget.start_at)}–${formatTime(removeTarget.end_at)} time off (${removeTarget.reason})? That time will become bookable again.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// -- Appointment type assignment (ADMIN only) ---------------------------

function AppointmentTypeAssignment({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [assigned, setAssigned] = useState<AppointmentType[]>([])
  const [catalog, setCatalog] = useState<AppointmentTypeSummary[]>([])
  const [selected, setSelected] = useState('')
  const [duration, setDuration] = useState(30)
  // Consultation fee (patient arrival workflow Phase 3) -- entered as a
  // plain rupee amount, not a placeholder/default the app invents; 0
  // means "not set yet" both here and on the backend
  // (get_consultation_charge_service).
  const [fee, setFee] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [removeTarget, setRemoveTarget] = useState<AppointmentType | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDuration, setEditDuration] = useState(30)
  const [editFee, setEditFee] = useState('')
  const [editBusy, setEditBusy] = useState(false)

  function load() {
    Promise.all([listAppointmentTypesForDoctor(doctor.id), listAppointmentTypeCatalog()])
      .then(([assignedList, all]) => {
        setAssigned(assignedList)
        setCatalog(all)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointment types'))
  }

  useEffect(load, [doctor.id])

  const unassigned = catalog.filter((c) => !assigned.some((a) => a.id === c.id))

  async function handleAssign(e: React.FormEvent) {
    e.preventDefault()
    if (!selected) return
    setError(null)
    try {
      await assignAppointmentTypeToDoctor(doctor.id, Number(selected), duration, Number(fee) || 0)
      resetAssignForm()
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not assign appointment type')
    }
  }

  function resetAssignForm() {
    setSelected('')
    setDuration(30)
    setFee('')
    setShowForm(false)
  }

  async function confirmRemove() {
    if (!removeTarget) return
    setError(null)
    try {
      await removeAppointmentTypeFromDoctor(doctor.id, removeTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove appointment type')
    } finally {
      setRemoveTarget(null)
    }
  }

  function startEdit(a: AppointmentType) {
    setEditingId(a.id)
    setEditDuration(a.duration_minutes)
    setEditFee(String(a.consultation_fee))
    setError(null)
  }

  async function saveEdit(e: React.FormEvent) {
    e.preventDefault()
    if (editingId === null) return
    setError(null)
    setEditBusy(true)
    try {
      await updateDoctorAppointmentType(doctor.id, editingId, editDuration, Number(editFee) || 0)
      setEditingId(null)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save this appointment type')
    } finally {
      setEditBusy(false)
    }
  }

  return (
    <div>
      <div className="admin-content-header">
        <div>
          <h4 style={{ margin: 0 }}>Appointment types</h4>
          <p className="muted" style={{ margin: 0 }}>
            The types of appointments this doctor offers, and how long each one takes.
          </p>
        </div>
        {isAdmin && unassigned.length > 0 && (
          <button type="button" className="btn btn-sm" onClick={() => setShowForm((v) => !v)}>
            {showForm ? 'Cancel' : '+ Add appointment type'}
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}

      <ul className="tag-list">
        {assigned.map((a) =>
          editingId === a.id ? (
            <li key={a.id}>
              <form className="inline-form" onSubmit={saveEdit} style={{ display: 'inline-flex', gap: 8 }}>
                <span>{a.name}</span>
                <input
                  type="number"
                  min={5}
                  max={240}
                  value={editDuration}
                  onChange={(e) => setEditDuration(Number(e.target.value))}
                  style={{ width: 70 }}
                  aria-label="Duration (minutes)"
                  required
                />
                <span className="muted">min · ₹</span>
                <input
                  type="number"
                  min={0}
                  value={editFee}
                  onChange={(e) => setEditFee(e.target.value)}
                  style={{ width: 80 }}
                  aria-label="Consultation fee"
                />
                <button type="submit" className="link" disabled={editBusy}>
                  {editBusy ? 'Saving…' : 'Save'}
                </button>
                <button type="button" className="link" onClick={() => setEditingId(null)}>
                  Cancel
                </button>
              </form>
            </li>
          ) : (
            <li key={a.id}>
              {a.name} ({a.duration_minutes} min · ₹{a.consultation_fee})
              {isAdmin && (
                <>
                  <button type="button" className="link" onClick={() => startEdit(a)}>
                    Edit
                  </button>
                  <button type="button" className="link" onClick={() => setRemoveTarget(a)}>
                    remove
                  </button>
                </>
              )}
            </li>
          ),
        )}
        {assigned.length === 0 && <li className="muted">No appointment types assigned.</li>}
      </ul>

      {isAdmin && showForm && unassigned.length > 0 && (
        <form className="inline-form wrap" onSubmit={handleAssign}>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} required>
            <option value="">Add appointment type…</option>
            {unassigned.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>

          <label className="inline-label">
            Duration
            <select value={duration} onChange={(e) => setDuration(Number(e.target.value))} style={{ width: 'auto' }}>
              {[5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60].map((d) => (
                <option key={d} value={d}>
                  {d} min
                </option>
              ))}
            </select>
            <span className="muted">Used when generating available appointment slots.</span>
          </label>

          <label className="inline-label">
            Consultation fee (₹)
            <input type="number" min={0} step="1" placeholder="0" value={fee} onChange={(e) => setFee(e.target.value)} />
          </label>

          <button type="submit" className="btn-sm">
            Save
          </button>
          <button type="button" className="btn-secondary btn btn-sm" onClick={resetAssignForm}>
            Cancel
          </button>
        </form>
      )}

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove appointment type?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget && `Stop offering ${removeTarget.name} for ${doctor.name}?`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
