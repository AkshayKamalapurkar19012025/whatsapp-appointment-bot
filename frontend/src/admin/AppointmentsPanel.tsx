import { Fragment, useEffect, useState } from 'react'
import { ClipboardText } from '@phosphor-icons/react'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import {
  ApiError,
  cancelAdminAppointment,
  completeAdminAppointment,
  confirmAdminAppointment,
  getDashboardStats,
  listAdminAppointments,
  listAllDoctors,
  listAppointmentTypeCatalog,
  listPatients,
  noShowAdminAppointment,
  rejectAdminAppointment,
  rescheduleAdminAppointment,
  visitAdminAppointment,
} from '../api'
import type { AdminAppointment, AppointmentTypeSummary, Doctor, DashboardStats, Patient, Slot } from '../types'
import { formatDate, formatTime, isoDateOnly } from '../format'
import AdminSlotPicker from './AdminSlotPicker'
import AppointmentDetailsModal from './AppointmentDetailsModal'
import { AppointmentActionButtons, buildAppointmentActions, type AppointmentActionHandlers } from './AppointmentActions'

// Radix Select.Item disallows an empty-string value (it's reserved
// internally for "no selection"), so the "All" filter option -- which
// maps to '' for the actual doctorFilter/etc. state, meaning "don't
// filter" -- needs a distinct sentinel value instead.
const ALL_FILTER_VALUE = '__all__'

// Duration in minutes between two ISO timestamps -- AdminAppointment
// doesn't carry duration_minutes directly (it's a doctor_appointment_
// types property, not an appointment column), and re-deriving it here is
// simpler than adding a field to the admin listing endpoint for a number
// this component can already compute from what it has.
function durationBetween(startAt: string, endAt: string): number {
  return Math.round((new Date(endAt).getTime() - new Date(startAt).getTime()) / 60000)
}

// "Upcoming" means still Pending or Confirmed (the two statuses that
// haven't happened, been rejected, or been cancelled yet -- see
// ACTIONABLE_STATUSES in app/services/appointment_services.py) and not
// yet started; a past Pending/Confirmed appointment falls out of
// Upcoming on its own without needing a status change. Shared by the
// tab filter and the tab count so the two can never disagree.
function isUpcoming(a: AdminAppointment, now: number): boolean {
  return ['PENDING', 'CONFIRMED'].includes(a.status) && new Date(a.start_at).getTime() >= now
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
  const [appointments, setAppointments] = useState<AdminAppointment[]>([])
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [patients, setPatients] = useState<Patient[]>([])
  const [appointmentTypes, setAppointmentTypes] = useState<AppointmentTypeSummary[]>([])
  // Today's/pending counts for the heading context line -- an existing
  // endpoint (DashboardPanel.tsx's own data source), not a new one;
  // best-effort only, so the heading just stays clean if it fails
  // rather than blocking or showing an error banner for a decoration.
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [tab, setTab] = useState<'upcoming' | 'all'>('upcoming')
  const [doctorFilter, setDoctorFilter] = useState('')
  const [patientFilter, setPatientFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [appointmentTypeFilter, setAppointmentTypeFilter] = useState('')
  const [dateFromFilter, setDateFromFilter] = useState('')
  const [dateToFilter, setDateToFilter] = useState('')
  const [searchText, setSearchText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [reschedulingId, setReschedulingId] = useState<number | null>(null)
  const [rescheduleSlot, setRescheduleSlot] = useState<Slot | null>(null)
  const [rescheduleBusy, setRescheduleBusy] = useState(false)
  const [detailsTarget, setDetailsTarget] = useState<AdminAppointment | null>(null)

  function load() {
    setLoading(true)
    setError(null)
    listAdminAppointments({
      doctor_id: doctorFilter ? Number(doctorFilter) : undefined,
      patient_id: patientFilter ? Number(patientFilter) : undefined,
      status: statusFilter || undefined,
      appointment_type_id: appointmentTypeFilter ? Number(appointmentTypeFilter) : undefined,
      date_from: dateFromFilter || undefined,
      date_to: dateToFilter || undefined,
    })
      .then(setAppointments)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : 'Could not load appointments'),
      )
      .finally(() => setLoading(false))
  }

  useEffect(load, [doctorFilter, patientFilter, statusFilter, appointmentTypeFilter, dateFromFilter, dateToFilter])

  useEffect(() => {
    listAllDoctors().then(setDoctors).catch(() => undefined)
    listPatients().then(setPatients).catch(() => undefined)
    listAppointmentTypeCatalog().then(setAppointmentTypes).catch(() => undefined)
    getDashboardStats().then(setStats).catch(() => undefined)
  }, [])

  // Free-text patient search and the Upcoming/All tab are both applied
  // client-side over whatever the server-side filters above already
  // narrowed down to -- the whole list is already loaded for this
  // panel, so a second round trip for a substring match or a "still in
  // the future" check would be pure overhead.
  const searchNeedle = searchText.trim().toLowerCase()
  const now = Date.now()
  function matchesSearch(a: AdminAppointment): boolean {
    if (!searchNeedle) return true
    return (
      a.patient_name.toLowerCase().includes(searchNeedle) || a.whatsapp_number.toLowerCase().includes(searchNeedle)
    )
  }
  const upcomingCount = appointments.filter((a) => isUpcoming(a, now) && matchesSearch(a)).length
  const allCount = appointments.filter(matchesSearch).length
  const visibleAppointments = appointments.filter((a) => {
    if (tab === 'upcoming' && !isUpcoming(a, now)) return false
    return matchesSearch(a)
  })
  // Keyed on `appointments` (the server-fetched list), not
  // `visibleAppointments` -- the latter also changes on every keystroke
  // of the client-side name/number search above, which would restage
  // the whole table mid-typing instead of just when the underlying data
  // actually reloads.
  const tbodyRef = useStaggerReveal<HTMLTableSectionElement>([appointments])
  const [cancelTarget, setCancelTarget] = useState<AdminAppointment | null>(null)
  const [lifecycleBusyId, setLifecycleBusyId] = useState<number | null>(null)

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
  // (Confirm/Reject/Check In/Complete) -- each is a single-column status
  // update with no follow-up form, unlike cancel (a confirm dialog) or
  // reschedule (a slot picker), so a plain busy-while-in-flight button
  // is enough.
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

  // One set of handlers, shared by the table row, the mobile card, and
  // the details modal (AppointmentActions.tsx's buildAppointmentActions)
  // -- so "what can I do with this appointment" is never computed two
  // different ways depending on where it's rendered.
  const actionHandlers: AppointmentActionHandlers = {
    onConfirm: (a) => runLifecycleAction(a.id, confirmAdminAppointment, 'Could not confirm the appointment'),
    onReject: (a) => runLifecycleAction(a.id, rejectAdminAppointment, 'Could not reject the appointment'),
    onCheckIn: (a) => runLifecycleAction(a.id, visitAdminAppointment, 'Could not check in the appointment'),
    onNoShow: (a) =>
      runLifecycleAction(a.id, noShowAdminAppointment, 'Could not mark the appointment as a no-show'),
    onComplete: (a) =>
      runLifecycleAction(a.id, completeAdminAppointment, 'Could not mark the appointment completed'),
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

  const activeFilterCount = [
    doctorFilter,
    patientFilter,
    statusFilter,
    appointmentTypeFilter,
    dateFromFilter,
    dateToFilter,
    searchText,
  ].filter(Boolean).length

  function clearFilters() {
    setDoctorFilter('')
    setPatientFilter('')
    setStatusFilter('')
    setAppointmentTypeFilter('')
    setDateFromFilter('')
    setDateToFilter('')
    setSearchText('')
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Appointments</h2>
          <p className="muted">Manage, view, filter, and take action on appointments.</p>
          {stats && (
            <p className="appointments-context-line">
              Today ·{' '}
              {formatDate(isoDateOnly(new Date().getFullYear(), new Date().getMonth() + 1, new Date().getDate()))} ·{' '}
              {stats.today_appointments} {stats.today_appointments === 1 ? 'appointment' : 'appointments'} ·{' '}
              {stats.pending_appointments} pending
            </p>
          )}
        </div>
        <button type="button" className="btn btn-sm" onClick={onBookAppointment}>
          + Book appointment
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="tabs">
        {(['upcoming', 'all'] as const).map((t) => (
          <button key={t} type="button" className={t === tab ? 'tab active' : 'tab'} onClick={() => setTab(t)}>
            {t === 'upcoming' ? `Upcoming (${upcomingCount})` : `All appointments (${allCount})`}
          </button>
        ))}
      </div>

      <div className="filter-bar">
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
                <SelectItem value={ALL_FILTER_VALUE}>All</SelectItem>
                {doctors.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="inline-label">
            Patient
            <Select
              value={patientFilter || ALL_FILTER_VALUE}
              onValueChange={(v) => setPatientFilter(v === ALL_FILTER_VALUE ? '' : v)}
            >
              <SelectTrigger className="filter-select-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_FILTER_VALUE}>All</SelectItem>
                {patients.map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.name}
                  </SelectItem>
                ))}
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
                <SelectItem value={ALL_FILTER_VALUE}>All</SelectItem>
                {appointmentTypes.map((t) => (
                  <SelectItem key={t.id} value={String(t.id)}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="inline-label">
            From
            <input type="date" value={dateFromFilter} onChange={(e) => setDateFromFilter(e.target.value)} />
          </label>
          <label className="inline-label">
            To
            <input type="date" value={dateToFilter} onChange={(e) => setDateToFilter(e.target.value)} />
          </label>
        </div>
        <div className="filter-bar-search-row">
          <label className="inline-label filter-bar-search">
            Search patient
            <input
              type="search"
              placeholder="Name or number"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />
          </label>
          {activeFilterCount > 0 && (
            <button type="button" className="btn-secondary btn btn-sm" onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading appointments…
        </div>
      )}
      {!loading && visibleAppointments.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <ClipboardText size={28} weight="light" />
          </span>
          {appointments.length === 0
            ? 'No appointments found.'
            : tab === 'upcoming'
              ? 'No upcoming appointments.'
              : 'No appointments match your search.'}
        </div>
      )}

      {!loading && visibleAppointments.length > 0 && (
        <>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Patient</th>
                  <th>Doctor</th>
                  <th>Type</th>
                  <th>When</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody ref={tbodyRef}>
                {visibleAppointments.map((a) => {
                  const actions = buildAppointmentActions(a, actionHandlers, isAdmin)
                  const busy = lifecycleBusyId === a.id
                  return (
                    <Fragment key={a.id}>
                      <tr className="appointment-row" onClick={() => setDetailsTarget(a)}>
                        <td>
                          <strong>{a.patient_name}</strong>
                          <div className="muted">{a.whatsapp_number}</div>
                        </td>
                        <td>{a.doctor_name}</td>
                        <td>{a.appointment_type_name}</td>
                        <td>
                          <div className="appointment-when">
                            <span className="appointment-when-date">{formatDate(a.start_at)}</span>
                            <span className="muted appointment-when-time">
                              {formatTime(a.start_at)} – {formatTime(a.end_at)}
                            </span>
                          </div>
                        </td>
                        <td>
                          <span className={`pill status-${a.status.toLowerCase()}`}>{a.status.replace(/_/g, ' ')}</span>
                          {paymentPill(a)}
                          {a.token_number !== null && <span className="pill token-pill">Token #{a.token_number}</span>}
                        </td>
                        <td onClick={(e) => e.stopPropagation()}>
                          <AppointmentActionButtons actions={actions} busy={busy} />
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
            {visibleAppointments.map((a) => {
              const actions = buildAppointmentActions(a, actionHandlers, isAdmin)
              const busy = lifecycleBusyId === a.id
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
                    {a.doctor_name} · {a.appointment_type_name}
                  </div>
                  <div className="appointment-when">
                    <span className="appointment-when-date">{formatDate(a.start_at)}</span>
                    <span className="muted appointment-when-time">
                      {formatTime(a.start_at)} – {formatTime(a.end_at)}
                    </span>
                  </div>
                  <div onClick={(e) => e.stopPropagation()}>
                    <AppointmentActionButtons actions={actions} busy={busy} />
                    {reschedulingId === a.id && reschedulePanel(a)}
                  </div>
                </li>
              )
            })}
          </ul>
        </>
      )}

      <AlertDialog open={cancelTarget !== null} onOpenChange={(open) => !open && setCancelTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel appointment?</AlertDialogTitle>
            <AlertDialogDescription>
              {cancelTarget &&
                `Cancel ${cancelTarget.patient_name}'s appointment with ${cancelTarget.doctor_name}?`}
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
