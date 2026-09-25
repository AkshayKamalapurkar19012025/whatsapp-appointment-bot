import { Fragment, useEffect, useState } from 'react'
import {
  ArrowLeft,
  Buildings,
  CaretDown,
  DotsThree,
  DotsThreeVertical,
  PencilSimple,
  Tag,
  Trash,
  UserCircle,
} from '@phosphor-icons/react'
import {
  ApiError,
  assignAppointmentTypeToDoctor,
  assignDoctorToDepartment,
  cancelAdminAppointment,
  completeAdminAppointment,
  confirmAdminAppointment,
  getDoctorDepartments,
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
  visitAdminAppointment,
} from '../api'
import type {
  AdminAppointment,
  AppointmentType,
  AppointmentTypeSummary,
  Department,
  Doctor,
  DoctorDepartmentAssignment,
  Slot,
} from '../types'
import { describeArrival, formatDate, formatTime, doctorSummaryLine } from '../format'
import DoctorAvatar from '../DoctorAvatar'
import DepartmentChip from '../DepartmentChip'
import { departmentIcon } from '../departmentIcon'
import { appointmentTypeIcon } from '../appointmentTypeIcon'
import { accentClassFor } from '../cardAccent'
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
import DoctorProfileSection from './DoctorProfileSection'
import AppointmentDetailsModal from './AppointmentDetailsModal'
import { AppointmentActionButtons, buildAppointmentActions, type AppointmentActionHandlers } from './AppointmentActions'
import VisitCompletionDialog from './VisitCompletionDialog'
import type { ConsultationTab } from './ConsultationWorkspace'
import { isoDateToday } from './doctorSchedule'
import ScheduleGrid from './ScheduleGrid'
import TimeOffSection from './TimeOffSection'

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
  onOpenConsultation,
}: {
  doctor: Doctor
  isAdmin: boolean
  onBack: () => void
  onGoToQueue?: (doctorId: number) => void
  // Passed through to the Appointments tab's VisitCompletionDialog --
  // see DoctorsPanel.tsx's own comment on this prop.
  onOpenConsultation?: (appointmentId: number, tab?: ConsultationTab) => void
}) {
  const [tab, setTab] = useState<WorkspaceTab>('overview')
  const [deactivateOpen, setDeactivateOpen] = useState(false)
  const [deactivating, setDeactivating] = useState(false)
  const [deactivateError, setDeactivateError] = useState<string | null>(null)
  // Gates the Schedule tab: without a real appointment type assigned,
  // the Working-Hours Preview has no real duration to chop slots by
  // (ScheduleGrid's own previewDurationMinutes falls back to the
  // doctor's disconnected default_duration_minutes -- see that file's
  // comment), which is exactly what produced the "preview slot time
  // doesn't match what's bookable" confusion this now heads off at the
  // source. null while loading, so the gate doesn't flash on before the
  // first fetch resolves. Refetched on every tab change (cheap, single
  // list call) rather than threaded through AppointmentTypeAssignment's
  // own state, so assigning a type on the Types tab and switching to
  // Schedule immediately reflects it.
  const [hasAppointmentTypes, setHasAppointmentTypes] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    listAppointmentTypesForDoctor(doctor.id)
      .then((types) => {
        if (!cancelled) setHasAppointmentTypes(types.length > 0)
      })
      .catch(() => {
        if (!cancelled) setHasAppointmentTypes(null)
      })
    return () => {
      cancelled = true
    }
  }, [doctor.id, tab])
  // The Schedule tab no longer stages edits into a page-wide draft --
  // every create/edit now persists immediately through the Configure
  // Schedule popup (its own discard-confirm guards unsaved *popup*
  // state), so tab switches and back-navigation no longer need a
  // separate dirty guard here. Kept as plain aliases so the many call
  // sites below don't need touching.
  function guardedSetTab(next: WorkspaceTab) {
    if (next === tab) return
    setTab(next)
  }

  function guardedOnBack() {
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
      {tab === 'appointments' && (
        <DoctorAppointmentsTab doctor={doctor} isAdmin={isAdmin} onOpenConsultation={onOpenConsultation} />
      )}
      {tab === 'schedule' &&
        (hasAppointmentTypes === false ? (
          <ScheduleNeedsAppointmentType onGoToTypes={() => guardedSetTab('types')} />
        ) : (
          <ScheduleGrid doctor={doctor} isAdmin={isAdmin} />
        ))}
      {tab === 'blocks' && <TimeOffSection doctor={doctor} />}
      {tab === 'departments' && <DepartmentAssignment doctor={doctor} isAdmin={isAdmin} />}
      {tab === 'types' && <AppointmentTypeAssignment doctor={doctor} isAdmin={isAdmin} />}
      {tab === 'profile' && <DoctorProfileSection doctor={doctor} isAdmin={isAdmin} />}
    </div>
  )
}

// The Schedule tab's gate -- shown instead of ScheduleGrid until this
// doctor has at least one real appointment type assigned. Scheduling
// without one used to fall back to a disconnected doctor-level preview
// duration, producing a Working-Hours Preview whose slot boundaries
// didn't match any real bookable time (the source of a real reported
// confusion). Requiring the type first removes that failure mode
// entirely instead of just labeling around it.
function ScheduleNeedsAppointmentType({ onGoToTypes }: { onGoToTypes: () => void }) {
  return (
    <div className="state-block empty">
      <span className="state-icon" aria-hidden="true">
        <Tag size={28} weight="light" />
      </span>
      <strong>Add an appointment type first</strong>
      <p className="muted" style={{ margin: 0, maxWidth: 360 }}>
        This doctor's schedule is chopped into bookable slots by an appointment type's real duration. Assign at
        least one before configuring working hours, so the preview always matches what patients can actually book.
      </p>
      <button type="button" className="btn btn-sm" onClick={onGoToTypes}>
        Go to Appointment types
      </button>
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
        setAppointments([...todaysAppointments.items].sort((a, b) => a.start_at.localeCompare(b.start_at)))
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

function DoctorAppointmentsTab({
  doctor,
  isAdmin,
  onOpenConsultation,
}: {
  doctor: Doctor
  isAdmin: boolean
  onOpenConsultation?: (appointmentId: number, tab?: ConsultationTab) => void
}) {
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
  const [completionTarget, setCompletionTarget] = useState<AdminAppointment | null>(null)
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
      // 'all' has no date bound (this doctor's entire history) -- the
      // one caller of this endpoint that realistically can exceed the
      // default page size, so request the max allowed page directly
      // rather than the 1000-row default every other, date-bounded
      // caller relies on.
      limit: when === 'all' ? 5000 : undefined,
    })
      .then(({ items: list }) => setAppointments([...list].sort((a, b) => a.start_at.localeCompare(b.start_at))))
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointments'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [doctor.id, when, statusFilter])

  useEffect(() => {
    const today = isoDateToday()
    listAdminAppointments({ doctor_id: doctor.id, date_from: today, date_to: today })
      .then(({ items }) => setTodaysAppointments(items))
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
    onComplete: (a) => setCompletionTarget(a),
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

      {completionTarget && (
        <VisitCompletionDialog
          appointmentId={completionTarget.id}
          patientName={completionTarget.patient_name}
          doctorName={completionTarget.doctor_name}
          onClose={() => setCompletionTarget(null)}
          onConfirm={() => {
            const target = completionTarget
            setCompletionTarget(null)
            runLifecycleAction(target.id, completeAdminAppointment, 'Could not mark the appointment completed')
          }}
          onGoToPending={onOpenConsultation ? (tabKey) => onOpenConsultation(completionTarget.id, tabKey) : undefined}
        />
      )}
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
        <form className="inline-form wrap assign-form-card" onSubmit={handleAssign}>
          <label className="inline-label">
            Department
            <select value={selected} onChange={(e) => setSelected(e.target.value)} required>
              <option value="">Add department…</option>
              {unassigned.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="btn btn-sm">
            + Add department
          </button>
          {selected && (
            <button type="button" className="btn-secondary btn btn-sm" onClick={() => setSelected('')}>
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

      {assigned.length === 0 ? (
        <p className="muted">No appointment types assigned yet.</p>
      ) : (
        <div className="appointment-type-card-list">
          {assigned.map((a) => {
            const Icon = appointmentTypeIcon(a.name)
            return editingId === a.id ? (
              <form
                key={a.id}
                className="appointment-type-card appointment-type-card-editing"
                onSubmit={saveEdit}
              >
                <span className={`department-card-icon ${accentClassFor(a.name)}`} aria-hidden="true">
                  <Icon size={18} weight="duotone" />
                </span>
                <div className="appointment-type-card-body">
                  <strong>{a.name}</strong>
                  <div className="inline-form">
                    <label className="inline-label">
                      Duration
                      <input
                        type="number"
                        min={5}
                        max={240}
                        value={editDuration}
                        onChange={(e) => setEditDuration(Number(e.target.value))}
                        style={{ width: 80 }}
                        aria-label="Duration (minutes)"
                        required
                      />
                    </label>
                    <label className="inline-label">
                      Fee (₹)
                      <input
                        type="number"
                        min={0}
                        value={editFee}
                        onChange={(e) => setEditFee(e.target.value)}
                        style={{ width: 90 }}
                        aria-label="Consultation fee"
                      />
                    </label>
                  </div>
                </div>
                <div className="appointment-type-card-actions">
                  <button type="submit" className="btn btn-sm" disabled={editBusy}>
                    {editBusy ? 'Saving…' : 'Save'}
                  </button>
                  <button type="button" className="btn-secondary btn btn-sm" onClick={() => setEditingId(null)}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <div key={a.id} className="appointment-type-card">
                <span className={`department-card-icon ${accentClassFor(a.name)}`} aria-hidden="true">
                  <Icon size={18} weight="duotone" />
                </span>
                <div className="appointment-type-card-body">
                  <strong>{a.name}</strong>
                  <span className="muted">
                    {a.duration_minutes} min · {a.consultation_fee > 0 ? `₹${a.consultation_fee}` : 'No fee set'}
                  </span>
                </div>
                {isAdmin && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button type="button" className="overflow-menu-trigger" aria-label={`Actions for ${a.name}`}>
                        <DotsThreeVertical size={18} weight="bold" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => startEdit(a)}>
                        <PencilSimple size={15} /> Edit
                      </DropdownMenuItem>
                      <DropdownMenuItem variant="danger" onSelect={() => setRemoveTarget(a)}>
                        <Trash size={15} /> Remove
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
            )
          })}
        </div>
      )}

      {isAdmin && showForm && unassigned.length > 0 && (
        <form className="inline-form wrap assign-form-card" onSubmit={handleAssign}>
          <label className="inline-label">
            Appointment type
            <select value={selected} onChange={(e) => setSelected(e.target.value)} required>
              <option value="">Add appointment type…</option>
              {unassigned.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>

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

          <button type="submit" className="btn btn-sm">
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
