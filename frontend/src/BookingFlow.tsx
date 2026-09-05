import { useEffect, useState } from 'react'
import { CheckCircle } from '@phosphor-icons/react'
import {
  ApiError,
  createWebAppointment,
  getSlotsForDate,
  listAppointmentTypesForDoctor,
  listDepartments,
  listDoctorsInDepartment,
  logout,
} from './api'
import type { AppointmentType, BookedAppointment, Department, Doctor, Slot } from './types'
import Calendar from './Calendar'
import SlotGrid from './SlotGrid'
import { formatDate, formatTime } from './format'

type Step =
  | 'department'
  | 'doctor'
  | 'appointmentType'
  | 'date'
  | 'slot'
  | 'review'
  | 'confirmation'

// Step order deliberately matches app/api/booking.py's actual WhatsApp
// flow (Department -> Doctor -> Appointment Type -> Date -> Slot), not
// the literal Department -> Doctor -> Date -> Slot -> Appointment Type
// order listed in the WEB P3 prompt text. That literal order isn't
// achievable as written: slot computation (both here and in WhatsApp)
// needs appointment_type_id already known, since each type has its own
// duration and duration determines the slot boundaries -- see
// app/services/availability_engine.py's get_available_slots signature.
// Reusing the exact same shared engine (global rule 4: don't duplicate
// booking logic between Web and WhatsApp) means reusing its real
// ordering constraint too. Flagged in the WEB P3 report, not a silent
// deviation.

export default function BookingFlow({
  patientName,
  onLoggedOut,
  onViewAppointments,
}: {
  patientName: string
  onLoggedOut: () => void
  onViewAppointments: () => void
}) {
  const [step, setStep] = useState<Step>('department')
  const [error, setError] = useState<string | null>(null)

  const [departments, setDepartments] = useState<Department[]>([])
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [appointmentTypes, setAppointmentTypes] = useState<AppointmentType[]>([])
  const [slots, setSlots] = useState<Slot[]>([])

  const [department, setDepartment] = useState<Department | null>(null)
  const [doctor, setDoctor] = useState<Doctor | null>(null)
  const [appointmentType, setAppointmentType] = useState<AppointmentType | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)
  const [confirmed, setConfirmed] = useState<BookedAppointment | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    listDepartments()
      .then(setDepartments)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load departments'))
  }, [])

  function chooseDepartment(d: Department) {
    setDepartment(d)
    setError(null)
    listDoctorsInDepartment(d.id)
      .then((result) => {
        setDoctors(result)
        setStep('doctor')
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctors'))
  }

  function chooseDoctor(doc: Doctor) {
    setDoctor(doc)
    setError(null)
    listAppointmentTypesForDoctor(doc.id)
      .then((result) => {
        setAppointmentTypes(result)
        setStep('appointmentType')
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointment types'))
  }

  function chooseAppointmentType(type: AppointmentType) {
    setAppointmentType(type)
    setError(null)
    setStep('date')
  }

  function chooseDate(isoDate: string) {
    if (!doctor || !appointmentType) return
    setSelectedDate(isoDate)
    setError(null)
    getSlotsForDate(doctor.id, appointmentType.id, isoDate, department?.id)
      .then((result) => {
        setSlots(result.slots)
        setStep('slot')
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load time slots'))
  }

  function chooseSlot(slot: Slot) {
    setSelectedSlot(slot)
    setStep('review')
  }

  async function confirmBooking() {
    if (!doctor || !appointmentType || !selectedSlot) return
    setBusy(true)
    setError(null)
    try {
      const result = await createWebAppointment(doctor.id, appointmentType.id, selectedSlot.start_at)
      setConfirmed(result)
      setStep('confirmation')
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        // Session expired or was revoked while mid-flow (e.g. the 24h
        // TTL elapsed, or logged out in another tab). Without this, the
        // user was stuck on the review screen forever: a generic error
        // message with no path back to the login screen, since nothing
        // in this component can otherwise get there.
        onLoggedOut()
        return
      }
      setError(err instanceof ApiError ? err.message : 'Could not book the appointment')
    } finally {
      setBusy(false)
    }
  }

  function startOver() {
    setStep('department')
    setDepartment(null)
    setDoctor(null)
    setAppointmentType(null)
    setSelectedDate(null)
    setSelectedSlot(null)
    setConfirmed(null)
    setError(null)
  }

  async function handleLogout() {
    await logout().catch(() => undefined)
    onLoggedOut()
  }

  return (
    <div className="card">
      <div className="topbar">
        <span>Hi, {patientName}</span>
        <div>
          <button type="button" className="link" onClick={onViewAppointments}>
            My appointments
          </button>
          <button type="button" className="link" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {step === 'department' && (
        <>
          <h2>Choose a department</h2>
          <ul className="option-list">
            {departments.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => chooseDepartment(d)}>
                  {d.name}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {step === 'doctor' && (
        <>
          <h2>Choose a doctor</h2>
          <ul className="option-list">
            {doctors.map((doc) => (
              <li key={doc.id}>
                <button type="button" onClick={() => chooseDoctor(doc)}>
                  {doc.name}
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="link" onClick={() => setStep('department')}>
            Back
          </button>
        </>
      )}

      {step === 'appointmentType' && (
        <>
          <h2>Choose an appointment type</h2>
          <ul className="option-list">
            {appointmentTypes.map((type) => (
              <li key={type.id}>
                <button type="button" onClick={() => chooseAppointmentType(type)}>
                  {type.name} <span className="muted">({type.duration_minutes} min)</span>
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="link" onClick={() => setStep('doctor')}>
            Back
          </button>
        </>
      )}

      {step === 'date' && doctor && appointmentType && (
        <>
          <h2>Choose a date</h2>
          <Calendar
            doctorId={doctor.id}
            appointmentTypeId={appointmentType.id}
            departmentId={department?.id}
            onSelectDate={chooseDate}
          />
          <button type="button" className="link" onClick={() => setStep('appointmentType')}>
            Back
          </button>
        </>
      )}

      {step === 'slot' && selectedDate && (
        <>
          <h2>Choose a time on {formatDate(selectedDate)}</h2>
          <SlotGrid slots={slots} onSelect={chooseSlot} />
          <button type="button" className="link" onClick={() => setStep('date')}>
            Back
          </button>
        </>
      )}

      {step === 'review' && department && doctor && appointmentType && selectedSlot && (
        <>
          <h2>Review your appointment</h2>
          <dl className="summary">
            <dt>Patient</dt>
            <dd>{patientName}</dd>
            <dt>Department</dt>
            <dd>{department.name}</dd>
            <dt>Doctor</dt>
            <dd>{doctor.name}</dd>
            <dt>Appointment type</dt>
            <dd>{appointmentType.name}</dd>
            <dt>Date</dt>
            <dd>{formatDate(selectedSlot.start_at)}</dd>
            <dt>Time</dt>
            <dd>
              {formatTime(selectedSlot.start_at)} – {formatTime(selectedSlot.end_at)}
            </dd>
            <dt>Duration</dt>
            <dd>{appointmentType.duration_minutes} minutes</dd>
          </dl>
          <button type="button" onClick={confirmBooking} disabled={busy}>
            {busy ? 'Booking…' : 'Confirm booking'}
          </button>
          <button type="button" className="link" onClick={() => setStep('slot')}>
            Back
          </button>
        </>
      )}

      {step === 'confirmation' && confirmed && doctor && department && selectedSlot && (
        <div className="confirmation">
          <div className="state-icon" aria-hidden="true">
            <CheckCircle size={40} weight="fill" color="var(--color-success)" />
          </div>
          <h2>Appointment Confirmed</h2>
          <dl className="summary">
            <dt>Doctor</dt>
            <dd>{doctor.name}</dd>
            <dt>Date</dt>
            {/* selectedSlot, not confirmed: the booking-creation response's
                start_at has round-tripped through Postgres and comes back
                UTC-normalized (a pre-existing characteristic of
                create_appointment_service, inherited unchanged from
                app/api/appointments.py -- never noticed before because
                nothing previously displayed it to a user). selectedSlot's
                start_at is the value already shown correctly in the doctor's
                local time during the "choose a time" step, computed
                straight from get_available_slots -- use that for display. */}
            <dd>{formatDate(selectedSlot.start_at)}</dd>
            <dt>Time</dt>
            <dd>{formatTime(selectedSlot.start_at)}</dd>
            <dt>Appointment Type</dt>
            <dd>{confirmed.appointment_type_name}</dd>
            <dt>Duration</dt>
            <dd>{confirmed.duration_minutes} minutes</dd>
          </dl>
          <p className="muted">
            A confirmation SMS has been sent to your registered number.
          </p>
          <button type="button" onClick={startOver}>
            Book another appointment
          </button>
          <button type="button" className="link" onClick={onViewAppointments}>
            View my appointments
          </button>
        </div>
      )}
    </div>
  )
}
