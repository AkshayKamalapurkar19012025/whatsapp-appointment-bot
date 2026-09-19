import { Fragment, useEffect, useState } from 'react'
import {
  CalendarBlank,
  CalendarCheck,
  CaretDown,
  CaretLeft,
  CaretRight,
  CheckCircle,
  Clock,
  FunnelSimple,
  HourglassMedium,
  ListNumbers,
  MagnifyingGlass,
  Plus,
  ArrowClockwise,
  UsersThree,
  Wallet,
  XCircle,
} from '@phosphor-icons/react'
import { useStaggerReveal } from '../useStaggerReveal'
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
import {
  ApiError,
  cancelAdminAppointment,
  completeAdminAppointment,
  confirmAdminAppointment,
  getDoctorQueue,
  listAdminAppointments,
  listAllDoctors,
  listDepartments,
  listDoctorsInDepartment,
  listPatients,
  markArrivedAdmin,
  noShowAdminAppointment,
  rejectAdminAppointment,
  rescheduleAdminAppointment,
  visitAdminAppointment,
} from '../api'
import type { AdminAppointment, Department, Doctor, DoctorQueue, Patient, Slot } from '../types'
import { formatAgeGender, formatDate, formatPatientId, formatTime, hasStarted } from '../format'
import { accentClassFor } from '../cardAccent'
import { isoDateToday } from './doctorSchedule'
import AdminSlotPicker from './AdminSlotPicker'
import AppointmentDetailsModal from './AppointmentDetailsModal'
import { AppointmentActionButtons, buildAppointmentActions, type AppointmentActionHandlers, type QueuePosition } from './AppointmentActions'

// Radix Select.Item disallows an empty-string value (reserved internally
// for "no selection") -- the "All" filter option, which maps to '' for
// the actual doctorFilter/etc. state (meaning "don't filter"), needs a
// distinct sentinel value instead.
const ALL_FILTER_VALUE = '__all__'

const PAGE_SIZE = 8

// This screen's own operational vocabulary (OPD Today redesign),
// mapped from -- never replacing -- the real backend status model
// (app/services/appointment_services.py: PENDING -> CONFIRMED ->
// CHECKED_IN -> COMPLETED, with REJECTED/CANCELLED/NO_SHOW exits).
// CHECKED_IN alone used to cover "just arrived, payment pending",
// "paid and waiting in the doctor's queue", and "actually being seen
// right now" all under one plain "Checked In" label -- opdStatus()
// below is the one place that splits it into the three distinct,
// front-desk-relevant buckets a receptionist actually needs to
// act on, using fields that already exist (payment_status,
// token_number, arrived_at) plus, for the waiting/serving split
// specifically, the SAME ordering rule app/api/doctors.py's
// get_doctor_queue already enforces (lowest token_number still
// CHECKED_IN = who's up). No new status is stored anywhere; this is a
// read-only, client-side view over data the admin appointments listing
// and the per-doctor queue endpoint already return.
type OpdStatus =
  | 'BOOKED'
  | 'LAPSED'
  | 'CONFIRMED'
  | 'ARRIVED'
  | 'WAITING'
  | 'IN_CONSULTATION'
  | 'CHECKED_IN'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'REJECTED'
  | 'NO_SHOW'

type StatusFilter = 'all' | OpdStatus

// Equal-weight tabs -- exactly the states a front-desk view needs to
// jump to constantly. Booked/Confirmed/Arrived/Rejected are real,
// preserved states (see opdStatus above / MORE_TABS below), just not
// equally urgent to have as permanent top-level real estate.
const PRIMARY_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'WAITING', label: 'Waiting' },
  { key: 'IN_CONSULTATION', label: 'In Consultation' },
  { key: 'COMPLETED', label: 'Completed' },
  { key: 'CANCELLED', label: 'Cancelled' },
  { key: 'NO_SHOW', label: 'No Show' },
]

const MORE_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'BOOKED', label: 'Booked' },
  { key: 'LAPSED', label: 'Lapsed' },
  { key: 'CONFIRMED', label: 'Confirmed' },
  { key: 'ARRIVED', label: 'Arrived' },
  { key: 'CHECKED_IN', label: 'Checked In' },
  { key: 'REJECTED', label: 'Rejected' },
]

type DateScope = 'today' | 'tomorrow' | 'week' | 'month' | 'custom'

function addDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00`)
  d.setDate(d.getDate() + days)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function startOfWeek(dateStr: string): string {
  const jsDay = new Date(`${dateStr}T00:00:00`).getDay()
  const mondayOffset = jsDay === 0 ? -6 : 1 - jsDay
  return addDays(dateStr, mondayOffset)
}

function startOfMonth(dateStr: string): string {
  return `${dateStr.slice(0, 7)}-01`
}

function endOfMonth(dateStr: string): string {
  const [y, m] = dateStr.split('-').map(Number)
  const lastDay = new Date(y, m, 0).getDate()
  return `${y}-${String(m).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`
}

const WEEKDAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

// "Thursday, 17 Sep 2026" -- the page header's own always-today date
// line (distinct from the date-scope filter's range label below it,
// which can point at a different day/range entirely).
function formatWeekdayDate(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00`)
  return `${WEEKDAY_NAMES[d.getDay()]}, ${formatDate(isoDate)}`
}

// The date-quick-filter row's from/to + a human label for the header --
// the one place "what does 'This week'/'This month' mean" is computed,
// shared by the summary cards, the tab counts, and the server query.
// Returns null only for an incomplete Custom Range (no from/to chosen
// yet), which callers treat as "don't fetch yet".
function dateRangeFor(
  scope: DateScope,
  customFrom: string,
  customTo: string,
): { from: string; to: string; label: string } | null {
  const today = isoDateToday()
  if (scope === 'today') return { from: today, to: today, label: `Today, ${formatDate(today)}` }
  if (scope === 'tomorrow') {
    const t = addDays(today, 1)
    return { from: t, to: t, label: `Tomorrow, ${formatDate(t)}` }
  }
  if (scope === 'week') {
    const from = startOfWeek(today)
    const to = addDays(from, 6)
    return { from, to, label: `This week, ${formatDate(from)} – ${formatDate(to)}` }
  }
  if (scope === 'month') {
    const from = startOfMonth(today)
    const to = endOfMonth(today)
    return { from, to, label: `This month, ${formatDate(from)} – ${formatDate(to)}` }
  }
  if (!customFrom || !customTo) return null
  return { from: customFrom, to: customTo, label: `${formatDate(customFrom)} – ${formatDate(customTo)}` }
}

// Duration in minutes between two ISO timestamps -- AdminAppointment
// doesn't carry duration_minutes directly (it's a doctor_appointment_
// types property, not an appointment column), and re-deriving it here is
// simpler than adding a field to the admin listing endpoint for a number
// this component can already compute from what it has.
function durationBetween(startAt: string, endAt: string): number {
  return Math.round((new Date(endAt).getTime() - new Date(startAt).getTime()) / 60000)
}

// The one place "what bucket is this appointment in, right now" is
// decided -- see the OpdStatus docstring above. queuePosition/
// isTodayScope both come from the live per-doctor queue fetch
// (fetchQueues below); every other field is already on AdminAppointment.
function opdStatus(a: AdminAppointment, queuePosition: Map<number, QueuePosition>, isTodayScope: boolean): OpdStatus {
  switch (a.status) {
    case 'PENDING':
      return hasStarted(a.start_at) ? 'LAPSED' : 'BOOKED'
    case 'CONFIRMED':
      return a.arrived_at ? 'ARRIVED' : 'CONFIRMED'
    case 'CHECKED_IN': {
      // Outside today, this is the only place a stale entry -- checked
      // in on some earlier day, never paid/waived into the queue or
      // completed -- becomes visible at all: get_doctor_queue
      // (app/api/doctors.py) scopes strictly to the doctor's current
      // local day, so it drops off that view the moment the day rolls
      // over, with no other surface. Kept as its own raw 'CHECKED_IN'
      // bucket (MORE_TABS below) rather than folded into
      // Waiting/Arrived/In Consultation -- those three only mean
      // anything relative to today's live queue, which a past day's
      // straggler was never part of.
      if (!isTodayScope) return 'CHECKED_IN'
      if (a.payment_status === 'UNPAID' || a.payment_status === 'FAILED') return 'ARRIVED'
      return queuePosition.get(a.id) === 'serving' ? 'IN_CONSULTATION' : 'WAITING'
    }
    case 'COMPLETED':
      return 'COMPLETED'
    case 'CANCELLED':
      return 'CANCELLED'
    case 'REJECTED':
      return 'REJECTED'
    case 'NO_SHOW':
      return 'NO_SHOW'
    default:
      return 'CHECKED_IN'
  }
}

const STATUS_PILL_LABEL: Record<OpdStatus, string> = {
  BOOKED: 'Booked',
  LAPSED: 'Lapsed',
  CONFIRMED: 'Confirmed',
  ARRIVED: 'Arrived',
  WAITING: 'Waiting',
  IN_CONSULTATION: 'In Consultation',
  CHECKED_IN: 'Checked In',
  COMPLETED: 'Completed',
  CANCELLED: 'Cancelled',
  REJECTED: 'Rejected',
  NO_SHOW: 'No Show',
}

// Reuses the existing lifecycle-status color tokens (styles.css's
// "Status pills" block) everywhere a direct equivalent already exists
// -- only WAITING and IN_CONSULTATION are genuinely new buckets with
// no prior single-word status to borrow a class from. LAPSED borrows
// NO_SHOW's color (same "expected, but nothing happened by the time
// that stopped being possible" shape), not a new one.
const STATUS_PILL_CLASS: Record<OpdStatus, string> = {
  BOOKED: 'pill status-pending',
  LAPSED: 'pill status-no_show',
  CONFIRMED: 'pill status-confirmed',
  ARRIVED: 'pill status-arrived',
  WAITING: 'pill status-waiting',
  IN_CONSULTATION: 'pill status-in-consultation',
  CHECKED_IN: 'pill status-checked_in',
  COMPLETED: 'pill status-completed',
  CANCELLED: 'pill status-cancelled',
  REJECTED: 'pill status-rejected',
  NO_SHOW: 'pill status-no_show',
}

function statusPill(a: AdminAppointment, queuePosition: Map<number, QueuePosition>, isTodayScope: boolean) {
  const status = opdStatus(a, queuePosition, isTodayScope)
  return <span className={STATUS_PILL_CLASS[status]}>{STATUS_PILL_LABEL[status]}</span>
}

// The one place "what does this row's Payment cell say" is decided --
// amount AND status together, never a bare badge disconnected from the
// number (redesign spec's explicit requirement). Reuses the exact same
// fields AppointmentDetailsModal's own PaymentSection already reads
// (consultation_fee/payment_amount/payment_status); this never invents
// a partial-payment state -- payment_status has no such value
// (app/services/appointment_services.py's record_payment_service
// records one all-or-nothing charge or waiver per appointment), so
// there is no "₹500 of ₹1,000 paid" case to render here.
function paymentCell(a: AdminAppointment) {
  const notApplicable = a.status === 'PENDING' || a.status === 'CONFIRMED' || a.status === 'CANCELLED' || a.status === 'REJECTED' || a.status === 'NO_SHOW'
  if (notApplicable) {
    return (
      <span className="opd-payment-cell">
        <span className="opd-payment-amount muted">—</span>
        <span className="pill payment-na">N/A</span>
      </span>
    )
  }
  if (a.payment_status === 'WAIVED') {
    return (
      <span className="opd-payment-cell">
        <span className="opd-payment-amount">₹{a.payment_amount ?? a.consultation_fee}</span>
        <span className="pill payment-waived">Waived</span>
      </span>
    )
  }
  if (a.payment_status === 'PAID') {
    return (
      <span className="opd-payment-cell">
        <span className="opd-payment-amount">₹{a.payment_amount ?? a.consultation_fee}</span>
        <span className="pill payment-paid">Paid</span>
      </span>
    )
  }
  return (
    <span className="opd-payment-cell">
      <span className="opd-payment-amount">₹{a.consultation_fee}</span>
      <span className={a.payment_status === 'FAILED' ? 'pill payment-failed' : 'pill payment-unpaid'}>
        {a.payment_status === 'FAILED' ? 'Failed' : 'Due'}
      </span>
    </span>
  )
}

export default function AppointmentsPanel({
  onBookAppointment,
  onRegisterNewPatient,
  onGoToQueue,
  onViewQueue,
  isAdmin,
}: {
  // Routes to the dedicated Book Appointment section (see AdminApp.tsx)
  // -- booking itself no longer happens inline on this page, which is
  // purely for viewing/filtering/managing appointments that already
  // exist. Also the destination for the "+ New OPD Visit" dropdown's
  // "Walk-in Registration" entry (that page already defaults its own
  // booking-source picker to Walk-in).
  onBookAppointment: () => void
  // "+ New OPD Visit > Register New Patient" -- routes to Book
  // Appointment's own find/register step (Step 1) instead of opening
  // PatientFormModal directly from here (OPD Patient Search &
  // Registration redesign, point 1: registration is never the starting
  // point of an OPD visit -- staff always search for an existing
  // patient first, even when they already know they need to register
  // someone new). AdminApp.tsx wires this to the same navigation as
  // onBookAppointment, plus a flag telling BookAppointmentPanel to open
  // its own "+ Register new patient" modal once Step 1 is showing.
  onRegisterNewPatient: () => void
  // Same "go to this doctor's live queue" hand-off DoctorsPanel's own
  // "View queue" action already uses (AdminApp.tsx's goToQueueForDoctor).
  onGoToQueue: (doctorId: number) => void
  // Routes to the standalone Queue section with no doctor preselected
  // (QueuePanel falls back to the last-viewed/first active doctor) --
  // Queue's own former sidebar entry (AdminApp.tsx), now reached only
  // from inside this OPD workspace, same as Book Appointment.
  onViewQueue: () => void
  // Gates "Waive Charge" (patient arrival workflow Phase 3) -- same
  // prop DoctorsPanel/DepartmentsPanel/AppointmentTypesPanel already
  // take from AdminApp.tsx.
  isAdmin: boolean
}) {
  // The date-scoped fetch: every appointment in the selected date
  // range, with NO doctor/status/department/search filter applied
  // server-side -- summary cards and tab counts both read from this
  // same list so they always agree with each other, and a doctor/
  // department/search filter never shifts either.
  const [dateScopedAppointments, setDateScopedAppointments] = useState<AdminAppointment[]>([])
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [doctorDepartments, setDoctorDepartments] = useState<Map<number, Department[]>>(new Map())
  // patient_id -> Patient, joined in purely for the age/gender line
  // under a patient's name -- AdminAppointment itself only carries
  // patient_name/whatsapp_number, not date_of_birth/gender (see
  // AppointmentsPanel's own deliverable notes on this).
  const [patients, setPatients] = useState<Map<number, Patient>>(new Map())
  // Doctor-scoped live queues (app/api/doctors.py's GET /doctors/{id}/
  // queue, the same endpoint QueueSection.tsx already polls) for every
  // doctor with at least one CHECKED_IN visit today -- the one source
  // both the Waiting/In Consultation KPI cards and each row's WAITING-
  // vs-IN_CONSULTATION split read from, so they can never disagree with
  // what QueuePanel itself would show for that doctor. Only fetched
  // when dateScope is 'today': the queue concept ("today's walk-in
  // queue") has no meaning for a week/month/custom range.
  const [queueByDoctor, setQueueByDoctor] = useState<Map<number, DoctorQueue>>(new Map())
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null)

  const [dateScope, setDateScope] = useState<DateScope>('today')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [doctorFilter, setDoctorFilter] = useState('')
  const [departmentFilter, setDepartmentFilter] = useState('')
  const [searchText, setSearchText] = useState('')
  const [page, setPage] = useState(1)

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [reschedulingId, setReschedulingId] = useState<number | null>(null)
  const [rescheduleSlot, setRescheduleSlot] = useState<Slot | null>(null)
  const [rescheduleBusy, setRescheduleBusy] = useState(false)
  const [detailsTarget, setDetailsTarget] = useState<AdminAppointment | null>(null)
  const [cancelTarget, setCancelTarget] = useState<AdminAppointment | null>(null)
  const [lifecycleBusyId, setLifecycleBusyId] = useState<number | null>(null)

  const range = dateRangeFor(dateScope, customFrom, customTo)
  const isTodayScope = dateScope === 'today'

  // Reused for every doctor with a CHECKED_IN visit today -- .catch
  // keeps one doctor's failed/404 (e.g. deactivated mid-shift) fetch
  // from breaking the whole aggregate, same defensive pattern
  // DoctorsPanel's own per-doctor Promise.all fan-outs already use.
  function fetchQueues(appointments: AdminAppointment[]) {
    if (!isTodayScope) {
      setQueueByDoctor(new Map())
      return Promise.resolve()
    }
    const doctorIds = Array.from(new Set(appointments.filter((a) => a.status === 'CHECKED_IN').map((a) => a.doctor_id)))
    if (doctorIds.length === 0) {
      setQueueByDoctor(new Map())
      return Promise.resolve()
    }
    return Promise.all(doctorIds.map((id) => getDoctorQueue(id).catch(() => null))).then((results) => {
      const map = new Map<number, DoctorQueue>()
      doctorIds.forEach((id, i) => {
        const result = results[i]
        if (result) map.set(id, result)
      })
      setQueueByDoctor(map)
    })
  }

  function load() {
    if (!range) {
      // Custom Range chosen but from/to not both picked yet -- nothing
      // to fetch, and showing a loading spinner for a query that isn't
      // happening would be misleading.
      setDateScopedAppointments([])
      setQueueByDoctor(new Map())
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    listAdminAppointments({ date_from: range.from, date_to: range.to })
      .then((list) => {
        const sorted = [...list].sort((a, b) => a.start_at.localeCompare(b.start_at))
        setDateScopedAppointments(sorted)
        return fetchQueues(sorted)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointments'))
      .finally(() => {
        setLoading(false)
        setLastUpdatedAt(new Date())
      })
  }

  useEffect(load, [range?.from, range?.to])

  useEffect(() => {
    listAllDoctors().then(setDoctors).catch(() => undefined)
    listPatients()
      .then((list) => setPatients(new Map(list.map((p) => [p.id, p]))))
      .catch(() => undefined)
    listDepartments().then((list) => {
      setDepartments(list)
      Promise.all(list.map((d) => listDoctorsInDepartment(d.id).catch(() => [] as Doctor[]))).then((perDept) => {
        const map = new Map<number, Department[]>()
        list.forEach((dept, i) => {
          for (const doc of perDept[i]) {
            map.set(doc.id, [...(map.get(doc.id) ?? []), dept])
          }
        })
        setDoctorDepartments(map)
      })
    })
  }, [])

  // A live workspace front-desk staff leave open during a shift, same
  // reasoning as QueueSection.tsx's own 20s poll -- longer here (this
  // reload does more work: the full date-range fetch plus every active
  // doctor's queue) and only while looking at Today, so a "This Month"
  // view doesn't keep re-fetching a much bigger, rarely-changing list
  // in the background.
  useEffect(() => {
    if (!isTodayScope) return
    const interval = setInterval(load, 30_000)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTodayScope])

  // Reset to page 1 whenever any filter (or the date scope itself)
  // changes -- staying on, say, page 3 after narrowing the list down to
  // one page's worth of rows would just show an empty page.
  useEffect(() => {
    setPage(1)
  }, [dateScope, customFrom, customTo, statusFilter, doctorFilter, departmentFilter, searchText])

  function doctorSpecialization(doctorId: number): string | null {
    return doctors.find((d) => d.id === doctorId)?.specialization ?? null
  }

  // appointment.id -> whether this doctor's queue currently has them as
  // "now serving" or merely "waiting" -- built once per render from
  // queueByDoctor, then reused for every row (status pill, primary
  // action) and the KPI cards below, so all of it reads one consistent
  // snapshot.
  const queuePosition = new Map<number, QueuePosition>()
  queueByDoctor.forEach((q) => {
    if (q.now_serving) queuePosition.set(q.now_serving.appointment_id, 'serving')
    q.waiting.forEach((entry) => queuePosition.set(entry.appointment_id, 'waiting'))
  })

  const searchNeedle = searchText.trim().toLowerCase()
  function matchesSearch(a: AdminAppointment): boolean {
    if (!searchNeedle) return true
    return (
      a.patient_name.toLowerCase().includes(searchNeedle) ||
      a.whatsapp_number.toLowerCase().includes(searchNeedle) ||
      formatPatientId(a.patient_id).toLowerCase().includes(searchNeedle) ||
      String(a.patient_id).includes(searchNeedle) ||
      String(a.id).includes(searchNeedle)
    )
  }

  // Tab/status counts -- deliberately over dateScopedAppointments (the
  // date-scoped set), not the doctor/department/search-filtered list
  // below, so picking a doctor never makes these numbers disagree with
  // the summary cards above.
  function countFor(status: StatusFilter): number {
    if (status === 'all') return dateScopedAppointments.length
    return dateScopedAppointments.filter((a) => opdStatus(a, queuePosition, isTodayScope) === status).length
  }

  const filteredAppointments = dateScopedAppointments.filter((a) => {
    if (statusFilter !== 'all' && opdStatus(a, queuePosition, isTodayScope) !== statusFilter) return false
    if (doctorFilter && a.doctor_id !== Number(doctorFilter)) return false
    if (departmentFilter && !(doctorDepartments.get(a.doctor_id) ?? []).some((d) => String(d.id) === departmentFilter)) return false
    return matchesSearch(a)
  })

  const totalPages = Math.max(1, Math.ceil(filteredAppointments.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const pagedAppointments = filteredAppointments.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  const tbodyRef = useStaggerReveal<HTMLTableSectionElement>([pagedAppointments])

  // -- KPI cards -----------------------------------------------------

  const totalVisits = dateScopedAppointments.length
  const walkInVisits = dateScopedAppointments.filter((a) => a.booking_source === 'WALK_IN').length
  const waitingCount = countFor('WAITING')
  const inConsultationCount = countFor('IN_CONSULTATION')
  const completedCount = countFor('COMPLETED')
  const noShowCount = countFor('NO_SHOW')

  // Real money, both figures -- never hard-coded (redesign spec's own
  // explicit requirement). Collected only counts payment_status PAID
  // (a WAIVED visit had no money change hands, so it's excluded from
  // "collected" the same way it's excluded from paymentPill's old
  // Unpaid-only warning). Pending mirrors the exact same CHECKED_IN +
  // UNPAID/FAILED gate paymentCell above uses -- a PENDING/CONFIRMED
  // appointment's fee isn't "pending" yet, it isn't even collectible
  // until the patient has arrived.
  const amountCollected = dateScopedAppointments
    .filter((a) => a.payment_status === 'PAID')
    .reduce((sum, a) => sum + (a.payment_amount ?? a.consultation_fee), 0)
  const amountPending = dateScopedAppointments
    .filter((a) => a.status === 'CHECKED_IN' && (a.payment_status === 'UNPAID' || a.payment_status === 'FAILED'))
    .reduce((sum, a) => sum + a.consultation_fee, 0)

  // Real average, from the live queue's own visited_at timestamps
  // (getDoctorQueue -- AdminAppointment itself has no check-in
  // timestamp to compute this from). null when nobody's currently
  // waiting, rather than showing a misleading "0 mins".
  let avgWaitMinutes: number | null = null
  if (isTodayScope) {
    const waits: number[] = []
    const now = Date.now()
    queueByDoctor.forEach((q) => {
      q.waiting.forEach((entry) => waits.push((now - new Date(entry.visited_at).getTime()) / 60000))
    })
    if (waits.length > 0) avgWaitMinutes = Math.round(waits.reduce((s, v) => s + v, 0) / waits.length)
  }

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

  // Shared handler for the one-click lifecycle transitions (Confirm/
  // Reject/Check In/Complete/No-Show/Mark Arrived) -- each is a single-
  // column status update with no follow-up form, unlike cancel (a
  // confirm dialog) or reschedule (a slot picker), so a plain
  // busy-while-in-flight button is enough.
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

  const reschedulingAppointment = dateScopedAppointments.find((a) => a.id === reschedulingId) ?? null

  // One set of handlers, shared by the table row, the mobile card, and
  // the details modal (AppointmentActions.tsx's buildAppointmentActions)
  // -- so "what can I do with this appointment" is never computed two
  // different ways depending on where it's rendered.
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
    // Both just open the details modal, where the real payment/waiver
    // form lives (AppointmentDetailsModal's own PaymentSection) --
    // neither is a single fire-and-forget action.
    onCollectPayment: setDetailsTarget,
    onWaiveCharge: setDetailsTarget,
    onOpenQueue: (a) => onGoToQueue(a.doctor_id),
  }

  function reschedulePanel(a: AdminAppointment) {
    if (reschedulingId !== a.id || !reschedulingAppointment) return null
    return (
      <div className="detail-section appointment-reschedule-panel">
        <h4>
          Reschedule {reschedulingAppointment.patient_name} with {reschedulingAppointment.doctor_name}
        </h4>
        <p className="muted">
          Currently {formatDate(reschedulingAppointment.start_at)} · {formatTime(reschedulingAppointment.start_at)} –{' '}
          {formatTime(reschedulingAppointment.end_at)}
        </p>
        <AdminSlotPicker
          doctorId={reschedulingAppointment.doctor_id}
          appointmentTypeId={reschedulingAppointment.appointment_type_id}
          durationMinutes={durationBetween(reschedulingAppointment.start_at, reschedulingAppointment.end_at)}
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
    )
  }

  const detailFiltersActive = Boolean(doctorFilter || departmentFilter || searchText)
  const anyFilterActive = detailFiltersActive || statusFilter !== 'all'

  function clearFilters() {
    setStatusFilter('all')
    setDoctorFilter('')
    setDepartmentFilter('')
    setSearchText('')
  }

  const activeTabLabel = [...PRIMARY_TABS, ...MORE_TABS].find((t) => t.key === statusFilter)?.label ?? 'All'

  function patientSubline(a: AdminAppointment): string | null {
    const patient = patients.get(a.patient_id)
    if (!patient) return null
    return formatAgeGender(patient.date_of_birth, patient.gender)
  }

  // "6:15 PM – 6:45 PM" alone when the date scope is already Today (a
  // repeated "17 Sep 2026" on every row of a same-day list is exactly
  // the "awkward multi-line date formatting" the redesign spec calls
  // out) -- the date line only earns its place back once the list can
  // span more than one day.
  function timeCell(a: AdminAppointment) {
    const timeRange = (
      <span className="muted appointment-when-time">
        <Clock size={13} weight="bold" aria-hidden="true" /> {formatTime(a.start_at)} – {formatTime(a.end_at)}
        <span className="opd-duration"> · {durationBetween(a.start_at, a.end_at)} mins</span>
      </span>
    )
    if (isTodayScope) return <div className="appointment-when">{timeRange}</div>
    return (
      <div className="appointment-when">
        <span className="appointment-when-date">
          <CalendarBlank size={13} weight="bold" aria-hidden="true" /> {formatDate(a.start_at)}
        </span>
        {timeRange}
      </div>
    )
  }

  return (
    <section className="opd-today">
      <div className="admin-content-header opd-today-header">
        <div>
          <h2>OPD Today</h2>
          <p className="opd-today-date">{formatWeekdayDate(isoDateToday())}</p>
        </div>
        <div className="opd-today-header-actions">
          <span className="opd-live-indicator">
            <span className="opd-live-dot" aria-hidden="true" /> Live
          </span>
          {lastUpdatedAt && (
            <span className="muted opd-last-updated">
              Last updated {lastUpdatedAt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
            </span>
          )}
          <button type="button" className="icon-btn" aria-label="Refresh" onClick={load} disabled={loading}>
            <ArrowClockwise size={17} weight="bold" className={loading ? 'opd-refresh-spinning' : undefined} />
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onViewQueue}>
            <ListNumbers size={15} weight="bold" /> Queue
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type="button" className="btn btn-sm">
                <Plus size={15} weight="bold" /> New OPD Visit <CaretDown size={12} weight="bold" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onBookAppointment}>Book Appointment</DropdownMenuItem>
              <DropdownMenuItem onSelect={onBookAppointment}>Walk-in Registration</DropdownMenuItem>
              <DropdownMenuItem onSelect={onRegisterNewPatient}>Register New Patient</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="dashboard-grid dashboard-grid-6 opd-kpi-grid">
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <CalendarCheck size={20} weight="bold" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{totalVisits}</span>
            <span className="stat-label">Total Visits</span>
            <span className="stat-sub">
              {totalVisits - walkInVisits} Appointments · {walkInVisits} Walk-ins
            </span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon tone-warning" aria-hidden="true">
            <HourglassMedium size={20} weight="bold" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{isTodayScope ? waitingCount : '—'}</span>
            <span className="stat-label">Waiting</span>
            <span className="stat-sub">
              {isTodayScope ? (avgWaitMinutes !== null ? `Avg. wait time ${avgWaitMinutes} mins` : 'Nobody waiting') : 'Live queue -- see Today'}
            </span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon tone-info" aria-hidden="true">
            <UsersThree size={20} weight="bold" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{isTodayScope ? inConsultationCount : '—'}</span>
            <span className="stat-label">In Consultation</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon tone-success" aria-hidden="true">
            <CheckCircle size={20} weight="bold" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{completedCount}</span>
            <span className="stat-label">Completed</span>
            {isTodayScope && <span className="stat-sub">Today</span>}
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon tone-danger" aria-hidden="true">
            <XCircle size={20} weight="bold" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{noShowCount}</span>
            <span className="stat-label">No Show</span>
            {isTodayScope && <span className="stat-sub">Today</span>}
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <Wallet size={20} weight="bold" />
          </span>
          <div className="stat-body">
            <span className="stat-value">₹{amountCollected.toLocaleString('en-IN')}</span>
            <span className="stat-label">Amount Collected</span>
            <span className="stat-sub">₹{amountPending.toLocaleString('en-IN')} Pending</span>
          </div>
        </div>
      </div>

      <div className="tabs appointments-tabs">
        {PRIMARY_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={t.key === statusFilter ? 'tab active' : 'tab'}
            onClick={() => setStatusFilter(t.key)}
          >
            {t.label} ({countFor(t.key)})
          </button>
        ))}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className={MORE_TABS.some((t) => t.key === statusFilter) ? 'tab active' : 'tab'}>
              More <CaretDown size={12} weight="bold" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            {MORE_TABS.map((t) => (
              <DropdownMenuItem key={t.key} onSelect={() => setStatusFilter(t.key)}>
                {t.label} ({countFor(t.key)})
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="date-scope-row">
        {(
          [
            ['today', 'Today'],
            ['tomorrow', 'Tomorrow'],
            ['week', 'This Week'],
            ['month', 'This Month'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={dateScope === key ? 'date-scope-pill active' : 'date-scope-pill'}
            onClick={() => setDateScope(key)}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          className={dateScope === 'custom' ? 'date-scope-pill active' : 'date-scope-pill'}
          onClick={() => setDateScope('custom')}
        >
          <CalendarBlank size={14} weight="bold" aria-hidden="true" /> Custom Range
        </button>
        {dateScope === 'custom' && (
          <span className="date-scope-custom-inputs">
            <label className="inline-label">
              From
              <input type="date" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} />
            </label>
            <label className="inline-label">
              To
              <input type="date" value={customTo} onChange={(e) => setCustomTo(e.target.value)} />
            </label>
          </span>
        )}
      </div>

      <div className="filter-bar appointments-filter-bar">
        <div className="filter-bar-fields">
          <label className="inline-label">
            Department
            <Select
              value={departmentFilter || ALL_FILTER_VALUE}
              onValueChange={(v) => setDepartmentFilter(v === ALL_FILTER_VALUE ? '' : v)}
            >
              <SelectTrigger className="filter-select-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_FILTER_VALUE}>All Departments</SelectItem>
                {departments.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="inline-label">
            Doctor
            <Select
              value={doctorFilter || ALL_FILTER_VALUE}
              onValueChange={(v) => setDoctorFilter(v === ALL_FILTER_VALUE ? '' : v)}
            >
              <SelectTrigger className="filter-select-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_FILTER_VALUE}>All Doctors</SelectItem>
                {doctors.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="inline-label filter-bar-search">
            Search patient
            <span className="filter-bar-search-input">
              <MagnifyingGlass size={15} aria-hidden="true" />
              <input
                type="search"
                placeholder="Name, UHID, phone number or appointment ID"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
              />
            </span>
          </label>
          <div className="filter-bar-toolbar-actions">
            {anyFilterActive && (
              <button type="button" className="btn-secondary btn btn-sm" onClick={clearFilters}>
                Reset
              </button>
            )}
          </div>
        </div>
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading appointments…
        </div>
      )}

      {!loading && dateScopedAppointments.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <CalendarBlank size={28} weight="light" />
          </span>
          <strong>No visits {range ? `for ${range.label}` : 'in this range'}</strong>
          <p className="muted">
            {range ? `There are no OPD visits scheduled for ${range.label}.` : 'Pick a From and To date to see visits in that range.'}
          </p>
          <button type="button" className="btn btn-sm" onClick={onBookAppointment}>
            + New OPD Visit
          </button>
        </div>
      )}

      {!loading && dateScopedAppointments.length > 0 && filteredAppointments.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <FunnelSimple size={28} weight="light" />
          </span>
          {detailFiltersActive ? (
            <>
              <strong>No visits match your filters</strong>
              <p className="muted">Try changing your filters or search criteria.</p>
              <button type="button" className="btn-secondary btn btn-sm" onClick={clearFilters}>
                Clear filters
              </button>
            </>
          ) : (
            <strong>
              No {activeTabLabel.toLowerCase()} visits {range ? `for ${range.label}` : ''}
            </strong>
          )}
        </div>
      )}

      {!loading && pagedAppointments.length > 0 && (
        <>
          <div className="data-table-wrap">
            <table className="data-table opd-table">
              <thead>
                <tr>
                  <th aria-hidden="true"></th>
                  <th>Patient</th>
                  <th>UHID</th>
                  <th>Doctor</th>
                  <th>Visit Type</th>
                  <th>Time</th>
                  <th>Payment</th>
                  <th>Status</th>
                  <th>Token</th>
                  <th className="opd-actions-cell">Actions</th>
                </tr>
              </thead>
              <tbody ref={tbodyRef}>
                {pagedAppointments.map((a) => {
                  const pos = queuePosition.get(a.id)
                  const actions = buildAppointmentActions(a, actionHandlers, isAdmin, isTodayScope ? (pos ?? 'waiting') : undefined)
                  const busy = lifecycleBusyId === a.id
                  const specialization = doctorSpecialization(a.doctor_id)
                  const subline = patientSubline(a)
                  return (
                    <Fragment key={a.id}>
                      <tr className="appointment-row opd-row" onClick={() => setDetailsTarget(a)}>
                        <td className="opd-avatar-cell">
                          <span className={`opd-avatar ${accentClassFor(a.patient_name)}`} aria-hidden="true">
                            {a.patient_name.charAt(0).toUpperCase()}
                          </span>
                        </td>
                        <td>
                          <strong>{a.patient_name}</strong>
                          {subline && <div className="muted">{subline}</div>}
                          <div className="muted">{a.whatsapp_number}</div>
                        </td>
                        <td className="muted">{formatPatientId(a.patient_id)}</td>
                        <td>
                          <strong>{a.doctor_name}</strong>
                          {specialization && <div className="muted">{specialization}</div>}
                        </td>
                        <td>{a.appointment_type_name}</td>
                        <td>{timeCell(a)}</td>
                        <td>{paymentCell(a)}</td>
                        <td>{statusPill(a, queuePosition, isTodayScope)}</td>
                        <td>{a.token_number !== null ? <span className="pill token-pill">#{a.token_number}</span> : <span className="muted">—</span>}</td>
                        <td className="opd-actions-cell" onClick={(e) => e.stopPropagation()}>
                          <AppointmentActionButtons actions={actions} busy={busy} compact />
                        </td>
                      </tr>
                      {reschedulingId === a.id && (
                        <tr onClick={(e) => e.stopPropagation()}>
                          <td colSpan={10}>{reschedulePanel(a)}</td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>

          <ul className="appointment-mobile-list">
            {pagedAppointments.map((a) => {
              const pos = queuePosition.get(a.id)
              const actions = buildAppointmentActions(a, actionHandlers, isAdmin, isTodayScope ? (pos ?? 'waiting') : undefined)
              const busy = lifecycleBusyId === a.id
              const specialization = doctorSpecialization(a.doctor_id)
              const subline = patientSubline(a)
              return (
                <li key={a.id} className="appointment-mobile-card" onClick={() => setDetailsTarget(a)}>
                  <div className="appointment-mobile-card-top">
                    {statusPill(a, queuePosition, isTodayScope)}
                    {a.token_number !== null && <span className="pill token-pill">#{a.token_number}</span>}
                  </div>
                  <strong>{a.patient_name}</strong>
                  {subline && <div className="muted">{subline}</div>}
                  <div className="muted">
                    {formatPatientId(a.patient_id)} · {a.whatsapp_number}
                  </div>
                  <div className="appointment-mobile-card-doctor">
                    {a.doctor_name}
                    {specialization ? ` · ${specialization}` : ''} · {a.appointment_type_name}
                  </div>
                  {timeCell(a)}
                  <div className="opd-mobile-payment">{paymentCell(a)}</div>
                  <div onClick={(e) => e.stopPropagation()}>
                    <AppointmentActionButtons actions={actions} busy={busy} compact />
                    {reschedulingId === a.id && reschedulePanel(a)}
                  </div>
                </li>
              )
            })}
          </ul>

          <div className="appointments-pagination">
            <span className="muted">
              Showing {(currentPage - 1) * PAGE_SIZE + 1} to{' '}
              {Math.min(currentPage * PAGE_SIZE, filteredAppointments.length)} of {filteredAppointments.length} visits
            </span>
            <span className="appointments-pagination-controls">
              <button
                type="button"
                className="icon-btn"
                aria-label="Previous page"
                disabled={currentPage <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <CaretLeft size={16} />
              </button>
              <span className="appointments-pagination-current" aria-current="page">
                {currentPage}
              </span>
              <button
                type="button"
                className="icon-btn"
                aria-label="Next page"
                disabled={currentPage >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                <CaretRight size={16} />
              </button>
            </span>
          </div>
        </>
      )}

      <p className="muted opd-footer-summary">
        {waitingCount} Patients waiting · Average wait time {avgWaitMinutes !== null ? `${avgWaitMinutes} mins` : '—'} ·{' '}
        {inConsultationCount} In consultation · {completedCount} Completed today · ₹{amountCollected.toLocaleString('en-IN')} collected
      </p>

      <AlertDialog open={cancelTarget !== null} onOpenChange={(open) => !open && setCancelTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel appointment?</AlertDialogTitle>
            <AlertDialogDescription>
              {cancelTarget && `Cancel ${cancelTarget.patient_name}'s appointment with ${cancelTarget.doctor_name}?`}
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

    </section>
  )
}
