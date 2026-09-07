import { useEffect, useState } from 'react'
import { CalendarBlank, CalendarCheck, ClockCounterClockwise, XCircle } from '@phosphor-icons/react'
import {
  ApiError,
  cancelWebAppointment,
  getMyAppointments,
  getSlotsForDate,
  rescheduleWebAppointment,
} from './api'
import type { MyAppointment, MyAppointmentsResponse, Slot } from './types'
import Calendar from './Calendar'
import PatientTopBar from './PatientTopBar'
import SlotGrid from './SlotGrid'
import StepActions from './StepActions'
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

// One status icon + tint per tab -- every card in a given tab shares
// the same status, so this is a function of the tab, not the item.
const TAB_ICON = {
  upcoming: { Icon: CalendarCheck, accent: 'accent-teal' },
  history: { Icon: ClockCounterClockwise, accent: 'accent-blue' },
  cancelled: { Icon: XCircle, accent: 'accent-rose' },
} as const

export default function MyAppointments({
  patientName,
  onLoggedOut,
  onScheduleNew,
}: {
  patientName: string
  onLoggedOut: () => void
  // "Main Menu" from the reschedule date-picker (StepActions) -- jumps
  // straight to starting a brand new appointment, the same escape-hatch
  // role "Main Menu" plays throughout SchedulingFlow.tsx. "Back" there
  // already exits the reschedule sub-flow (cancelRescheduleFlow), so
  // this is the one action that isn't otherwise reachable from here.
  onScheduleNew: () => void
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
        // Same lesson as SchedulingFlow.confirmScheduling (WEB P3 second-pass
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

  // The patient's other upcoming appointments with this same doctor --
  // feeds Calendar's same-doctor duplicate-appointment marker/nudge
  // below, same as SchedulingFlow.tsx's Doctor-First date step. Excludes
  // the appointment actually being rescheduled itself: it will always
  // land on its own existing date/doctor, which isn't a real conflict,
  // just the record about to move.
  const otherAppointmentsWithRescheduleDoctor = rescheduling
    ? (data?.upcoming ?? []).filter((a) => a.doctor_id === rescheduling.doctor_id && a.id !== rescheduling.id)
    : []

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

  const list: MyAppointment[] = data ? data[tab] : []
  const listRef = useStaggerReveal<HTMLUListElement>([list])

  return (
    <div className="card patient-card">
      <PatientTopBar patientName={patientName} subtitle="Manage your upcoming and past visits." />

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
                doctorName={rescheduling.doctor_name}
                existingAppointments={otherAppointmentsWithRescheduleDoctor}
              />
              <StepActions onBack={cancelRescheduleFlow} onMainMenu={onScheduleNew} />
            </>
          )}

          {rescheduleStep === 'slot' && (
            <>
              <h3>Choose a new time</h3>
              <SlotGrid slots={rescheduleSlots} onSelect={chooseRescheduleSlot} />
              <StepActions onBack={() => setRescheduleStep('date')} onMainMenu={onScheduleNew} />
            </>
          )}

          {rescheduleStep === 'review' && selectedSlot && (
            <>
              <h3>Confirm new time</h3>
              {/* Same summary shape as SchedulingFlow.tsx's review step,
                  minus Department: an appointment is only ever tied to a
                  doctor_id + appointment_type_id (see
                  list_patient_appointments_service), never a
                  department_id, and reschedule doesn't re-pick one --
                  there's nothing there to show, not a gap. */}
              <dl className="summary">
                <dt>Patient</dt>
                <dd>{patientName}</dd>
                <dt>Doctor</dt>
                <dd>{rescheduling.doctor_name}</dd>
                <dt>Appointment type</dt>
                <dd>{rescheduling.appointment_type_name}</dd>
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
              <StepActions onBack={() => setRescheduleStep('slot')} onMainMenu={onScheduleNew} />
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
            {list.map((appointment) => {
              const { Icon: TabIcon, accent } = TAB_ICON[tab]
              return (
                <li key={appointment.id} className="appointment-card">
                  <span className={`appointment-card-icon ${accent}`} aria-hidden="true">
                    <TabIcon size={20} weight="duotone" />
                  </span>
                  <div className="appointment-card-body">
                    <strong>{appointment.doctor_name}</strong>
                    {appointment.doctor_specialization && (
                      <span className="option-subtitle">{appointment.doctor_specialization}</span>
                    )}
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
