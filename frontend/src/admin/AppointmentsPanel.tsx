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
  listAdminAppointments,
  listAllDoctors,
  listAppointmentTypeCatalog,
  listPatients,
  noShowAdminAppointment,
  rejectAdminAppointment,
  rescheduleAdminAppointment,
  visitAdminAppointment,
} from '../api'
import type { AdminAppointment, AppointmentTypeSummary, Doctor, Patient, Slot } from '../types'
import { formatDate, formatTime, hasStarted } from '../format'
import AdminSlotPicker from './AdminSlotPicker'

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

export default function AppointmentsPanel({
  onBookAppointment,
}: {
  // Routes to the dedicated Book Appointment section (see AdminApp.tsx)
  // -- booking itself no longer happens inline on this page, which is
  // purely for viewing/filtering/managing appointments that already
  // exist.
  onBookAppointment: () => void
}) {
  const [appointments, setAppointments] = useState<AdminAppointment[]>([])
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [patients, setPatients] = useState<Patient[]>([])
  const [appointmentTypes, setAppointmentTypes] = useState<AppointmentTypeSummary[]>([])
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
  }, [])

  // Free-text patient search and the Upcoming/All tab are both applied
  // client-side over whatever the server-side filters above already
  // narrowed down to -- the whole list is already loaded for this
  // panel, so a second round trip for a substring match or a "still in
  // the future" check would be pure overhead. "Upcoming" means still
  // Pending or Confirmed (the two statuses that haven't happened, been
  // rejected, or been cancelled yet -- see ACTIONABLE_STATUSES in
  // app/services/appointment_services.py) and not yet started; a past
  // Pending/Confirmed appointment falls out of Upcoming on its own
  // without needing a status change.
  const searchNeedle = searchText.trim().toLowerCase()
  const now = Date.now()
  const visibleAppointments = appointments.filter((a) => {
    if (
      tab === 'upcoming' &&
      (!['PENDING', 'CONFIRMED'].includes(a.status) || new Date(a.start_at).getTime() < now)
    )
      return false
    if (!searchNeedle) return true
    return (
      a.patient_name.toLowerCase().includes(searchNeedle) || a.whatsapp_number.toLowerCase().includes(searchNeedle)
    )
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
  // (Confirm/Reject/Visit/Complete) -- each is a single-column status
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
    setReschedulingId(appointment.id)
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

  return (
    <section>
      <div className="admin-content-header">
        <h2>Appointments</h2>
        <button type="button" className="btn btn-sm" onClick={onBookAppointment}>
          + Book appointment
        </button>
      </div>
      <p className="muted">View, filter, reschedule, and cancel existing appointments.</p>
      {error && <p className="error">{error}</p>}

      <div className="tabs">
        {(['upcoming', 'all'] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={t === tab ? 'tab active' : 'tab'}
            onClick={() => setTab(t)}
          >
            {t === 'upcoming' ? 'Upcoming' : 'All'}
          </button>
        ))}
      </div>

      <div className="filter-bar">
        <label className="inline-label">
          Doctor
          <Select
            value={doctorFilter || ALL_FILTER_VALUE}
            onValueChange={(v) => setDoctorFilter(v === ALL_FILTER_VALUE ? '' : v)}
          >
            <SelectTrigger>
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
            <SelectTrigger>
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
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_FILTER_VALUE}>All</SelectItem>
              <SelectItem value="PENDING">Pending</SelectItem>
              <SelectItem value="CONFIRMED">Confirmed</SelectItem>
              <SelectItem value="REJECTED">Rejected</SelectItem>
              <SelectItem value="CANCELLED">Cancelled</SelectItem>
              <SelectItem value="CHECKED_IN">Check In</SelectItem>
              <SelectItem value="COMPLETED">Completed</SelectItem>
              <SelectItem value="NO_SHOW">No-Show</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <label className="inline-label">
          Appointment type
          <Select
            value={appointmentTypeFilter || ALL_FILTER_VALUE}
            onValueChange={(v) => setAppointmentTypeFilter(v === ALL_FILTER_VALUE ? '' : v)}
          >
            <SelectTrigger>
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
        <label className="inline-label">
          Search patient
          <input
            type="search"
            placeholder="Name or number"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </label>
        {(doctorFilter || patientFilter || statusFilter || appointmentTypeFilter || dateFromFilter || dateToFilter || searchText) && (
          <button
            type="button"
            className="btn-secondary btn btn-sm"
            onClick={() => {
              setDoctorFilter('')
              setPatientFilter('')
              setStatusFilter('')
              setAppointmentTypeFilter('')
              setDateFromFilter('')
              setDateToFilter('')
              setSearchText('')
            }}
          >
            Clear filters
          </button>
        )}
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
            {visibleAppointments.map((a) => (
              <Fragment key={a.id}>
                <tr>
                  <td>
                    {a.patient_name}
                    <div className="muted">{a.whatsapp_number}</div>
                  </td>
                  <td>{a.doctor_name}</td>
                  <td>{a.appointment_type_name}</td>
                  <td>
                    {formatDate(a.start_at)} · {formatTime(a.start_at)} – {formatTime(a.end_at)}
                  </td>
                  <td>
                    <span className={`pill status-${a.status.toLowerCase()}`}>{a.status.replace(/_/g, ' ')}</span>
                    {a.token_number !== null && <span className="pill token-pill">Token #{a.token_number}</span>}
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                      {a.status === 'PENDING' && (
                        <>
                          <button
                            type="button"
                            className="link"
                            disabled={lifecycleBusyId === a.id}
                            onClick={() =>
                              runLifecycleAction(a.id, confirmAdminAppointment, 'Could not confirm the appointment')
                            }
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            className="link danger"
                            disabled={lifecycleBusyId === a.id}
                            onClick={() =>
                              runLifecycleAction(a.id, rejectAdminAppointment, 'Could not reject the appointment')
                            }
                          >
                            Reject
                          </button>
                        </>
                      )}
                      {a.status === 'CONFIRMED' && hasStarted(a.start_at) && (
                        <>
                          <button
                            type="button"
                            className="link"
                            disabled={lifecycleBusyId === a.id}
                            onClick={() =>
                              runLifecycleAction(a.id, visitAdminAppointment, 'Could not check in the appointment')
                            }
                          >
                            Check In
                          </button>
                          <button
                            type="button"
                            className="link danger"
                            disabled={lifecycleBusyId === a.id}
                            onClick={() =>
                              runLifecycleAction(
                                a.id,
                                noShowAdminAppointment,
                                'Could not mark the appointment as a no-show',
                              )
                            }
                          >
                            Mark No-Show
                          </button>
                        </>
                      )}
                      {a.status === 'CONFIRMED' && !hasStarted(a.start_at) && (
                        <span className="muted">Not started yet</span>
                      )}
                      {a.status === 'CHECKED_IN' && (
                        <button
                          type="button"
                          className="link"
                          disabled={lifecycleBusyId === a.id}
                          onClick={() =>
                            runLifecycleAction(
                              a.id,
                              completeAdminAppointment,
                              'Could not mark the appointment completed',
                            )
                          }
                        >
                          Mark completed
                        </button>
                      )}
                      {(a.status === 'PENDING' || a.status === 'CONFIRMED') && (
                        <>
                          <button
                            type="button"
                            className="link"
                            onClick={() =>
                              reschedulingId === a.id ? setReschedulingId(null) : startReschedule(a)
                            }
                          >
                            {reschedulingId === a.id ? 'Close' : 'Reschedule'}
                          </button>
                          <button type="button" className="link danger" onClick={() => setCancelTarget(a)}>
                            Cancel
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
                {reschedulingId === a.id && reschedulingAppointment && (
                  <tr>
                    <td colSpan={6}>
                      <div className="detail-section" style={{ margin: '8px 0' }}>
                        <h4>
                          Reschedule {reschedulingAppointment.patient_name} with{' '}
                          {reschedulingAppointment.doctor_name}
                        </h4>
                        <p className="muted">
                          Currently {formatDate(reschedulingAppointment.start_at)} ·{' '}
                          {formatTime(reschedulingAppointment.start_at)} –{' '}
                          {formatTime(reschedulingAppointment.end_at)}
                        </p>
                        <AdminSlotPicker
                          doctorId={reschedulingAppointment.doctor_id}
                          appointmentTypeId={reschedulingAppointment.appointment_type_id}
                          durationMinutes={durationBetween(
                            reschedulingAppointment.start_at,
                            reschedulingAppointment.end_at,
                          )}
                          selectedSlot={rescheduleSlot}
                          onSelect={setRescheduleSlot}
                        />
                        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                          <button
                            type="button"
                            className="btn btn-sm"
                            disabled={!rescheduleSlot || rescheduleBusy}
                            onClick={() => confirmReschedule(a.id)}
                          >
                            {rescheduleBusy ? 'Saving…' : 'Confirm new time'}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary btn btn-sm"
                            onClick={() => setReschedulingId(null)}
                          >
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
    </section>
  )
}
