import { useEffect, useState } from 'react'
import { CalendarBlank } from '@phosphor-icons/react'
import {
  ApiError,
  cancelWebAppointment,
  getMyAppointments,
  getSlotsForDate,
  logout,
  rescheduleWebAppointment,
} from './api'
import type { MyAppointment, MyAppointmentsResponse, Slot } from './types'
import Calendar from './Calendar'
import PatientTopBar from './PatientTopBar'
import SlotGrid from './SlotGrid'
import { formatDate, formatTime } from './format'
import { useStaggerReveal } from './useStaggerReveal'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './components/ui/alert-dialog'

type Tab = 'upcoming' | 'history' | 'cancelled'
type RescheduleStep = 'date' | 'slot' | 'review' | 'done'

export default function MyAppointments({
  patientName,
  onLoggedOut,
  onBookNew,
}: {
  patientName: string
  onLoggedOut: () => void
  onBookNew: () => void
}) {
  const [tab, setTab] = useState<Tab>('upcoming')
  const [data, setData] = useState<MyAppointmentsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Reschedule sub-flow state -- null means "not currently rescheduling".
  const [rescheduling, setRescheduling] = useState<MyAppointment | null>(null)
  const [rescheduleStep, setRescheduleStep] = useState<RescheduleStep>('date')
  const [rescheduleSlots, setRescheduleSlots] = useState<Slot[]>([])
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)
  const [busy, setBusy] = useState(false)

  function load() {
    setLoading(true)
    setError(null)
    getMyAppointments()
      .then(setData)
      .catch((err) => {
        // Same lesson as BookingFlow.confirmBooking (WEB P3 second-pass
        // fix): an expired/invalid session here must return to login,
        // not leave the patient stuck on a page that can never load.
        if (err instanceof ApiError && err.status === 401) {
          onLoggedOut()
          return
        }
        setError(err instanceof ApiError ? err.message : 'Could not load your appointments')
      })
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const [cancelTarget, setCancelTarget] = useState<MyAppointment | null>(null)

  async function confirmCancel() {
    if (!cancelTarget) return
    setError(null)
    try {
      await cancelWebAppointment(cancelTarget.id)
      load()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onLoggedOut()
        return
      }
      setError(err instanceof ApiError ? err.message : 'Could not cancel the appointment')
    } finally {
      setCancelTarget(null)
    }
  }

  function startReschedule(appointment: MyAppointment) {
    setRescheduling(appointment)
    setRescheduleStep('date')
    setSelectedSlot(null)
    setError(null)
  }

  function cancelRescheduleFlow() {
    setRescheduling(null)
    setRescheduleSlots([])
    setSelectedSlot(null)
  }

  function chooseRescheduleDate(isoDate: string) {
    if (!rescheduling) return
    setError(null)
    getSlotsForDate(rescheduling.doctor_id, rescheduling.appointment_type_id, isoDate)
      .then((result) => {
        setRescheduleSlots(result.slots)
        setRescheduleStep('slot')
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load time slots'))
  }

  function chooseRescheduleSlot(slot: Slot) {
    setSelectedSlot(slot)
    setRescheduleStep('review')
  }

  async function confirmReschedule() {
    if (!rescheduling || !selectedSlot) return
    setBusy(true)
    setError(null)
    try {
      await rescheduleWebAppointment(rescheduling.id, selectedSlot.start_at)
      setRescheduleStep('done')
      load()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onLoggedOut()
        return
      }
      setError(err instanceof ApiError ? err.message : 'Could not reschedule the appointment')
    } finally {
      setBusy(false)
    }
  }

  async function handleLogout() {
    await logout().catch(() => undefined)
    onLoggedOut()
  }

  const list: MyAppointment[] = data ? data[tab] : []
  const listRef = useStaggerReveal<HTMLUListElement>([list])

  return (
    <div className="card">
      <PatientTopBar
        patientName={patientName}
        subtitle="Manage your upcoming and past visits."
        actions={
          <>
            <button type="button" className="link" onClick={onBookNew}>
              Book an appointment
            </button>
            <button type="button" className="link" onClick={handleLogout}>
              Log out
            </button>
          </>
        }
      />

      {error && <p className="error">{error}</p>}

      {rescheduling ? (
        <>
          <h2>Reschedule with {rescheduling.doctor_name}</h2>
          <p className="muted">
            Currently: {formatDate(rescheduling.start_at)} at {formatTime(rescheduling.start_at)}
          </p>

          {rescheduleStep === 'date' && (
            <>
              <Calendar
                doctorId={rescheduling.doctor_id}
                appointmentTypeId={rescheduling.appointment_type_id}
                onSelectDate={chooseRescheduleDate}
              />
              <button type="button" className="link" onClick={cancelRescheduleFlow}>
                Cancel
              </button>
            </>
          )}

          {rescheduleStep === 'slot' && (
            <>
              <h3>Choose a new time</h3>
              <SlotGrid slots={rescheduleSlots} onSelect={chooseRescheduleSlot} />
              <button type="button" className="link" onClick={() => setRescheduleStep('date')}>
                Back
              </button>
            </>
          )}

          {rescheduleStep === 'review' && selectedSlot && (
            <>
              <h3>Confirm new time</h3>
              <dl className="summary">
                <dt>New date</dt>
                <dd>{formatDate(selectedSlot.start_at)}</dd>
                <dt>New time</dt>
                <dd>
                  {formatTime(selectedSlot.start_at)} – {formatTime(selectedSlot.end_at)}
                </dd>
              </dl>
              <button type="button" onClick={confirmReschedule} disabled={busy}>
                {busy ? 'Rescheduling…' : 'Confirm reschedule'}
              </button>
              <button type="button" className="link" onClick={() => setRescheduleStep('slot')}>
                Back
              </button>
            </>
          )}

          {rescheduleStep === 'done' && (
            <>
              <p>Your appointment has been rescheduled.</p>
              <button type="button" onClick={cancelRescheduleFlow}>
                Back to My Appointments
              </button>
            </>
          )}
        </>
      ) : (
        <>
          <h2>My Appointments</h2>
          <div className="tabs">
            {(['upcoming', 'history', 'cancelled'] as Tab[]).map((t) => (
              <button
                key={t}
                type="button"
                className={t === tab ? 'tab active' : 'tab'}
                onClick={() => setTab(t)}
              >
                {t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>

          {loading && (
            <div className="state-block">
              <span className="spinner" aria-hidden="true" />
              Loading…
            </div>
          )}

          {!loading && list.length === 0 && (
            <div className="state-block empty">
              <span className="state-icon" aria-hidden="true">
                <CalendarBlank size={28} weight="light" />
              </span>
              Nothing here yet.
            </div>
          )}

          <ul className="appointment-list" ref={listRef}>
            {list.map((appointment) => (
              <li key={appointment.id} className="appointment-card">
                <div>
                  <strong>{appointment.doctor_name}</strong>
                  <div className="muted">{appointment.appointment_type_name}</div>
                  <div>
                    {formatDate(appointment.start_at)} · {formatTime(appointment.start_at)} –{' '}
                    {formatTime(appointment.end_at)}
                  </div>
                  {appointment.token_number !== null && (
                    <div className="muted">Token #{appointment.token_number}</div>
                  )}
                </div>
                {tab === 'upcoming' && (
                  <div className="appointment-actions">
                    <button type="button" onClick={() => startReschedule(appointment)}>
                      Reschedule
                    </button>
                    <button type="button" className="danger" onClick={() => setCancelTarget(appointment)}>
                      Cancel
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      <AlertDialog open={cancelTarget !== null} onOpenChange={(open) => !open && setCancelTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel appointment?</AlertDialogTitle>
            <AlertDialogDescription>
              {cancelTarget &&
                `Cancel your ${formatDate(cancelTarget.start_at)} appointment with ${cancelTarget.doctor_name}?`}
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
