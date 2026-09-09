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
  MagnifyingGlass,
  UserCheck,
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
  listAdminAppointments,
  listAllDoctors,
  listAppointmentTypeCatalog,
  noShowAdminAppointment,
  rejectAdminAppointment,
  rescheduleAdminAppointment,
  visitAdminAppointment,
} from '../api'
import type { AdminAppointment, AppointmentTypeSummary, Doctor, Slot } from '../types'
import { formatDate, formatTime } from '../format'
import { isoDateToday } from './doctorSchedule'
import AdminSlotPicker from './AdminSlotPicker'
import AppointmentDetailsModal from './AppointmentDetailsModal'
import { AppointmentActionButtons, buildAppointmentActions, type AppointmentActionHandlers } from './AppointmentActions'

// Radix Select.Item disallows an empty-string value (reserved internally
// for "no selection") -- the "All" filter option, which maps to '' for
// the actual doctorFilter/etc. state (meaning "don't filter"), needs a
// distinct sentinel value instead.
const ALL_FILTER_VALUE = '__all__'

const PAGE_SIZE = 8

// Every real appointment status this app has (see app/services/
// appointment_services.py), plus 'all' -- one status filter, driven by
// EITHER the tab row or the compact Status dropdown, never two
// independently-tracked sources of truth that could disagree with each
// other. The Rejected/No Show statuses live behind the tab row's own
// "More" menu (not equal top-level tabs, per the operational split from
// Confirmed/Cancelled -- they're rare, and Cancelled already means
// "never happened" without conflating it with a doctor's own rejection
// or a patient's no-show), but they're still first-class values here.
type StatusFilter = 'all' | 'PENDING' | 'CONFIRMED' | 'CHECKED_IN' | 'COMPLETED' | 'CANCELLED' | 'REJECTED' | 'NO_SHOW'

const PRIMARY_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'PENDING', label: 'Pending' },
  { key: 'CONFIRMED', label: 'Confirmed' },
  { key: 'CHECKED_IN', label: 'Checked In' },
  { key: 'COMPLETED', label: 'Completed' },
  { key: 'CANCELLED', label: 'Cancelled' },
]

const MORE_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'REJECTED', label: 'Rejected' },
  { key: 'NO_SHOW', label: 'No Show' },
]

const STATUS_DROPDOWN_OPTIONS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All statuses' },
  ...PRIMARY_TABS.slice(1),
  ...MORE_TABS,
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

// Payment status is only a meaningful thing to show once a patient has
// arrived (patient arrival workflow Phases 3-5) -- a PENDING/CONFIRMED
// row showing "Unpaid" would just be noise before payment is even
// collectible, so this renders nothing for either of those statuses.
function paymentPill(a: AdminAppointment) {
  if (a.status !== 'CHECKED_IN' && a.status !== 'COMPLETED') return null
  const label =
    a.payment_status === 'PAID' ? 'Paid' : a.payment_status === 'WAIVED' ? 'Waived' : a.payment_status.replace(/_/g, ' ')
  return <span className={`pill payment-${a.payment_status.toLowerCase()}`}>{label}</span>
}

export default function AppointmentsPanel({
  onBookAppointment,
  isAdmin,
}: {
  // Routes to the dedicated Book Appointment section (see AdminApp.tsx)
  // -- booking itself no longer happens inline on this page, which is
  // purely for viewing/filtering/managing appointments that already
  // exist.
  onBookAppointment: () => void
  // Gates "Waive Charge" (patient arrival workflow Phase 3) -- same
  // prop DoctorsPanel/DepartmentsPanel/AppointmentTypesPanel already
  // take from AdminApp.tsx.
  isAdmin: boolean
}) {
  // The date-scoped fetch: every appointment in the selected date
  // range, with NO doctor/status/type/search filter applied server-side
  // -- summary cards and tab counts both read from this same list so
  // they always agree with each other, and a doctor/status/type/search
  // filter never shifts either (see dateRangeFor's docstring / the
  // redesign plan: "date scope drives the cards, detail filters filter
  // the list below").
  const [dateScopedAppointments, setDateScopedAppointments] = useState<AdminAppointment[]>([])
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [appointmentTypes, setAppointmentTypes] = useState<AppointmentTypeSummary[]>([])

  const [dateScope, setDateScope] = useState<DateScope>('today')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [doctorFilter, setDoctorFilter] = useState('')
  const [appointmentTypeFilter, setAppointmentTypeFilter] = useState('')
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

  function load() {
    if (!range) {
      // Custom Range chosen but from/to not both picked yet -- nothing
      // to fetch, and showing a loading spinner for a query that isn't
      // happening would be misleading.
      setDateScopedAppointments([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    listAdminAppointments({ date_from: range.from, date_to: range.to })
      .then((list) => setDateScopedAppointments([...list].sort((a, b) => a.start_at.localeCompare(b.start_at))))
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointments'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [range?.from, range?.to])

  useEffect(() => {
    listAllDoctors().then(setDoctors).catch(() => undefined)
    listAppointmentTypeCatalog().then(setAppointmentTypes).catch(() => undefined)
  }, [])

  // Reset to page 1 whenever any filter (or the date scope itself)
  // changes -- staying on, say, page 3 after narrowing the list down to
  // one page's worth of rows would just show an empty page.
  useEffect(() => {
    setPage(1)
  }, [dateScope, customFrom, customTo, statusFilter, doctorFilter, appointmentTypeFilter, searchText])

  function doctorSpecialization(doctorId: number): string | null {
    return doctors.find((d) => d.id === doctorId)?.specialization ?? null
  }

  const searchNeedle = searchText.trim().toLowerCase()
  function matchesSearch(a: AdminAppointment): boolean {
    if (!searchNeedle) return true
    return (
      a.patient_name.toLowerCase().includes(searchNeedle) ||
      a.whatsapp_number.toLowerCase().includes(searchNeedle) ||
      String(a.patient_id).includes(searchNeedle)
    )
  }

  // Tab/status counts -- deliberately over dateScopedAppointments (the
  // date-scoped set), not the doctor/type/search-filtered list below,
  // so picking a doctor never makes these numbers disagree with the
  // summary cards above.
  function countFor(status: StatusFilter): number {
    if (status === 'all') return dateScopedAppointments.length
    return dateScopedAppointments.filter((a) => a.status === status).length
  }

  const filteredAppointments = dateScopedAppointments.filter((a) => {
    if (statusFilter !== 'all' && a.status !== statusFilter) return false
    if (doctorFilter && a.doctor_id !== Number(doctorFilter)) return false
    if (appointmentTypeFilter && a.appointment_type_id !== Number(appointmentTypeFilter)) return false
    return matchesSearch(a)
  })

  const totalPages = Math.max(1, Math.ceil(filteredAppointments.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const pagedAppointments = filteredAppointments.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  const tbodyRef = useStaggerReveal<HTMLTableSectionElement>([pagedAppointments])

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

  // Shared handler for the four one-click lifecycle transitions
  // (Confirm/Reject/Check In/Complete/No-Show) -- each is a single-
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

  const detailFiltersActive = Boolean(doctorFilter || appointmentTypeFilter || searchText)
  const anyFilterActive = detailFiltersActive || statusFilter !== 'all'

  function clearFilters() {
    setStatusFilter('all')
    setDoctorFilter('')
    setAppointmentTypeFilter('')
    setSearchText('')
  }

  const activeTabLabel = [...PRIMARY_TABS, ...MORE_TABS].find((t) => t.key === statusFilter)?.label ?? 'All'

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Appointments</h2>
          <p className="muted">Manage appointments, bookings and patient visits.</p>
        </div>
        <button type="button" className="btn btn-sm" onClick={onBookAppointment}>
          + Book appointment
        </button>
      </div>

      {range && (
        <p className="appointments-date-scope-line">
          <CalendarBlank size={15} weight="bold" aria-hidden="true" /> {range.label}
        </p>
      )}

      {error && <p className="error">{error}</p>}

      <div className="dashboard-grid appointments-summary-grid">
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <CalendarCheck size={22} weight="regular" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{countFor('all')}</span>
            <span className="stat-label">Total appointments</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <HourglassMedium size={22} weight="regular" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{countFor('PENDING')}</span>
            <span className="stat-label">Pending</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <UserCheck size={22} weight="regular" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{countFor('CONFIRMED')}</span>
            <span className="stat-label">Confirmed</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <CheckCircle size={22} weight="regular" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{countFor('COMPLETED')}</span>
            <span className="stat-label">Completed</span>
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
            <button
              type="button"
              className={MORE_TABS.some((t) => t.key === statusFilter) ? 'tab active' : 'tab'}
            >
              More ({countFor('REJECTED') + countFor('NO_SHOW')}) <CaretDown size={12} weight="bold" />
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
            Doctor
            <Select
              value={doctorFilter || ALL_FILTER_VALUE}
              onValueChange={(v) => setDoctorFilter(v === ALL_FILTER_VALUE ? '' : v)}
            >
              <SelectTrigger className="filter-select-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_FILTER_VALUE}>All doctors</SelectItem>
                {doctors.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="inline-label">
            Status
            <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as StatusFilter)}>
              <SelectTrigger className="filter-select-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_DROPDOWN_OPTIONS.map((o) => (
                  <SelectItem key={o.key} value={o.key}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="inline-label">
            Appointment type
            <Select
              value={appointmentTypeFilter || ALL_FILTER_VALUE}
              onValueChange={(v) => setAppointmentTypeFilter(v === ALL_FILTER_VALUE ? '' : v)}
            >
              <SelectTrigger className="filter-select-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_FILTER_VALUE}>All types</SelectItem>
                {appointmentTypes.map((t) => (
                  <SelectItem key={t.id} value={String(t.id)}>
                    {t.name}
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
                placeholder="Name, phone number or patient ID"
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
          <strong>No appointments {range ? `for ${range.label}` : 'in this range'}</strong>
          <p className="muted">
            {range
              ? `There are no appointments scheduled for ${range.label}.`
              : 'Pick a From and To date to see appointments in that range.'}
          </p>
          <button type="button" className="btn btn-sm" onClick={onBookAppointment}>
            + Book appointment
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
              <strong>No appointments match your filters</strong>
              <p className="muted">Try changing your filters or search criteria.</p>
              <button type="button" className="btn-secondary btn btn-sm" onClick={clearFilters}>
                Clear filters
              </button>
            </>
          ) : (
            <strong>No {activeTabLabel.toLowerCase()} appointments {range ? `for ${range.label}` : ''}</strong>
          )}
        </div>
      )}

      {!loading && pagedAppointments.length > 0 && (
        <>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Patient</th>
                  <th>Doctor</th>
                  <th>Date &amp; Time</th>
                  <th>Appointment Type</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody ref={tbodyRef}>
                {pagedAppointments.map((a) => {
                  const actions = buildAppointmentActions(a, actionHandlers, isAdmin)
                  const busy = lifecycleBusyId === a.id
                  const specialization = doctorSpecialization(a.doctor_id)
                  return (
                    <Fragment key={a.id}>
                      <tr className="appointment-row" onClick={() => setDetailsTarget(a)}>
                        <td>
                          <strong>{a.patient_name}</strong>
                          <div className="muted">{a.whatsapp_number}</div>
                        </td>
                        <td>
                          <strong>{a.doctor_name}</strong>
                          {specialization && <div className="muted">{specialization}</div>}
                        </td>
                        <td>
                          <div className="appointment-when">
                            <span className="appointment-when-date">
                              <CalendarBlank size={13} weight="bold" aria-hidden="true" /> {formatDate(a.start_at)}
                            </span>
                            <span className="muted appointment-when-time">
                              <Clock size={13} weight="bold" aria-hidden="true" /> {formatTime(a.start_at)} –{' '}
                              {formatTime(a.end_at)}
                            </span>
                          </div>
                        </td>
                        <td>{a.appointment_type_name}</td>
                        <td>
                          <span className={`pill status-${a.status.toLowerCase()}`}>{a.status.replace(/_/g, ' ')}</span>
                          {paymentPill(a)}
                          {a.token_number !== null && <span className="pill token-pill">Token #{a.token_number}</span>}
                        </td>
                        <td onClick={(e) => e.stopPropagation()}>
                          <AppointmentActionButtons actions={actions} busy={busy} compact />
                        </td>
                      </tr>
                      {reschedulingId === a.id && (
                        <tr onClick={(e) => e.stopPropagation()}>
                          <td colSpan={6}>{reschedulePanel(a)}</td>
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
              const actions = buildAppointmentActions(a, actionHandlers, isAdmin)
              const busy = lifecycleBusyId === a.id
              const specialization = doctorSpecialization(a.doctor_id)
              return (
                <li key={a.id} className="appointment-mobile-card" onClick={() => setDetailsTarget(a)}>
                  <div className="appointment-mobile-card-top">
                    <span className={`pill status-${a.status.toLowerCase()}`}>{a.status.replace(/_/g, ' ')}</span>
                    {paymentPill(a)}
                    {a.token_number !== null && <span className="pill token-pill">Token #{a.token_number}</span>}
                  </div>
                  <strong>{a.patient_name}</strong>
                  <div className="muted">{a.whatsapp_number}</div>
                  <div className="appointment-mobile-card-doctor">
                    {a.doctor_name}
                    {specialization ? ` · ${specialization}` : ''} · {a.appointment_type_name}
                  </div>
                  <div className="appointment-when">
                    <span className="appointment-when-date">
                      <CalendarBlank size={13} weight="bold" aria-hidden="true" /> {formatDate(a.start_at)}
                    </span>
                    <span className="muted appointment-when-time">
                      <Clock size={13} weight="bold" aria-hidden="true" /> {formatTime(a.start_at)} – {formatTime(a.end_at)}
                    </span>
                  </div>
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
              {Math.min(currentPage * PAGE_SIZE, filteredAppointments.length)} of {filteredAppointments.length} appointments
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
