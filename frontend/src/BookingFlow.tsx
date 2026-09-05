import { useEffect, useState } from 'react'
import { CheckCircle } from '@phosphor-icons/react'
import {
  ApiError,
  createWebAppointment,
  getDoctorsForDate,
  getSlotsForDate,
  listAppointmentTypesForDepartment,
  listAppointmentTypesForDoctor,
  listDepartments,
  listDoctorsInDepartment,
  logout,
} from './api'
import type { BookedAppointment, Department, Doctor, DoctorWithSlots, Slot } from './types'
import Calendar from './Calendar'
import DepartmentCalendar from './DepartmentCalendar'
import DoctorCard from './DoctorCard'
import DoctorProfileModal from './DoctorProfileModal'
import LiveClock from './LiveClock'
import SlotGrid from './SlotGrid'
import { formatDate, formatTime } from './format'

type BookingMode = 'doctor-first' | 'date-first'

// A minimal shape covering both the Doctor-First appointment-type list
// (app/api/doctor_appointment_types.py's GET, duration_minutes always
// present -- it's a per-doctor assignment) and the Date-First one
// (app/services/availability_engine.get_appointment_types_for_department,
// no duration_minutes -- duration is per doctor+type, not meaningful
// before a doctor is chosen). One shared "selected type" state can hold
// either, since every place that reads duration_minutes from it only
// does so in a Doctor-First-only render branch (see the appointmentType
// step below) -- everywhere else (booking calls, review labels) only
// ever needs id/name, which both shapes always have.
interface SelectableAppointmentType {
  id: number
  name: string
  duration_minutes?: number
}

// Doctor-First's doctor list already carries active/created_at/
// created_by (GET /departments/{id}/doctors); Date-First's per-date
// doctor list (GET /web/availability/by-date) only ever has id/name.
// The SELECTED doctor is held in this narrower shape since nothing
// downstream (booking calls, review labels) needs more than that.
interface SelectableDoctor {
  id: number
  name: string
}

type Step =
  | 'mode'
  | 'department'
  | 'doctor'
  | 'appointmentType'
  | 'date'
  | 'availableDoctors'
  | 'slot'
  | 'review'
  | 'confirmation'

// Doctor-First's step order deliberately matches app/api/booking.py's
// WhatsApp Doctor-First flow (Department -> Doctor -> Appointment Type
// -> Date -> Slot); Date-First's matches its WhatsApp counterpart too
// (Department -> Appointment Type -> Date -> Available Doctors -> Slot).
// Both converge onto the same Slot -> Review -> Confirm tail and the
// same POST /web/appointments call -- one booking engine, two entry
// orders, exactly like the WhatsApp side (see app/api/booking.py's
// _select_date_or_available_doctors_response and its module-level
// comments for the equivalent server-side design).

export default function BookingFlow({
  patientName,
  onLoggedOut,
  onViewAppointments,
}: {
  patientName: string
  onLoggedOut: () => void
  onViewAppointments: () => void
}) {
  const [step, setStep] = useState<Step>('mode')
  const [mode, setMode] = useState<BookingMode | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [departments, setDepartments] = useState<Department[]>([])
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [appointmentTypes, setAppointmentTypes] = useState<SelectableAppointmentType[]>([])
  const [availableDoctors, setAvailableDoctors] = useState<DoctorWithSlots[]>([])
  const [slots, setSlots] = useState<Slot[]>([])

  const [department, setDepartment] = useState<Department | null>(null)
  const [doctor, setDoctor] = useState<SelectableDoctor | null>(null)
  const [appointmentType, setAppointmentType] = useState<SelectableAppointmentType | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)
  const [confirmed, setConfirmed] = useState<BookedAppointment | null>(null)
  const [busy, setBusy] = useState(false)
  // "View Profile" from a compact doctor card (DoctorCard.tsx) -- an
  // overlay on top of whichever step is currently showing, not a Step
  // of its own, so closing it never changes where the patient is in
  // the booking flow.
  const [viewingProfileDoctorId, setViewingProfileDoctorId] = useState<number | null>(null)

  useEffect(() => {
    listDepartments()
      .then(setDepartments)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load departments'))
  }, [])

  function chooseMode(chosen: BookingMode) {
    setMode(chosen)
    setError(null)
    setStep('department')
  }

  function chooseDepartment(d: Department) {
    setDepartment(d)
    setError(null)
    if (mode === 'date-first') {
      listAppointmentTypesForDepartment(d.id)
        .then((result) => {
          setAppointmentTypes(result)
          setStep('appointmentType')
        })
        .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointment types'))
      return
    }
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

  function chooseAppointmentType(type: SelectableAppointmentType) {
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

  function chooseDateFirstDate(isoDate: string) {
    if (!department || !appointmentType) return
    setSelectedDate(isoDate)
    setError(null)
    getDoctorsForDate(department.id, appointmentType.id, isoDate)
      .then((result) => {
        setAvailableDoctors(result.doctors)
        setStep('availableDoctors')
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load available doctors'))
  }

  function chooseAvailableDoctor(d: DoctorWithSlots) {
    setDoctor({ id: d.id, name: d.name })
    setSlots(d.slots)
    setError(null)
    setStep('slot')
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
    setStep('mode')
    setMode(null)
    setDepartment(null)
    setDoctor(null)
    setAppointmentType(null)
    setSelectedDate(null)
    setSelectedSlot(null)
    setAvailableDoctors([])
    setConfirmed(null)
    setError(null)
  }

  async function handleLogout() {
    await logout().catch(() => undefined)
    onLoggedOut()
  }

  // Slot-step "back": Doctor-First returns to its own per-doctor
  // calendar (Date); Date-First returns to Available Doctors (pick a
  // different doctor for the same date), not the calendar -- the one
  // place both flows share a step (Slot) but need different "back"
  // targets, matching app/api/booking.py's SELECT_SLOT back-handler
  // (_select_date_or_available_doctors_response).
  function backFromSlot() {
    setStep(mode === 'date-first' ? 'availableDoctors' : 'date')
  }

  return (
    <div className="card">
      <div className="topbar">
        <span>Hi, {patientName}</span>
        <div className="topbar-right">
          <LiveClock />
          <div className="topbar-actions">
            <button type="button" className="link" onClick={onViewAppointments}>
              My appointments
            </button>
            <button type="button" className="link" onClick={handleLogout}>
              Log out
            </button>
          </div>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {step === 'mode' && (
        <>
          <h2>How would you like to find your appointment?</h2>
          <ul className="option-list">
            <li>
              <button type="button" onClick={() => chooseMode('doctor-first')}>
                Choose a Doctor
                <span className="option-subtitle">You already know which doctor you'd like to see</span>
              </button>
            </li>
            <li>
              <button type="button" onClick={() => chooseMode('date-first')}>
                Find by Date
                <span className="option-subtitle">See which doctors are available on your preferred date</span>
              </button>
            </li>
          </ul>
        </>
      )}

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
          <div className="step-actions">
            <button type="button" className="link" onClick={() => setStep('mode')}>
              Back
            </button>
            <button type="button" className="link" onClick={startOver}>
              Main Menu
            </button>
          </div>
        </>
      )}

      {step === 'doctor' && (
        <>
          <h2>Choose a doctor</h2>
          <ul className="option-list">
            {doctors.map((doc) => (
              <DoctorCard
                key={doc.id}
                doctor={doc}
                onSelect={() => chooseDoctor(doc)}
                onViewProfile={() => setViewingProfileDoctorId(doc.id)}
              />
            ))}
          </ul>
          <div className="step-actions">
            <button type="button" className="link" onClick={() => setStep('department')}>
              Back
            </button>
            <button type="button" className="link" onClick={startOver}>
              Main Menu
            </button>
          </div>
        </>
      )}

      {step === 'appointmentType' && (
        <>
          <h2>Choose an appointment type</h2>
          <ul className="option-list">
            {appointmentTypes.map((type) => (
              <li key={type.id}>
                <button type="button" onClick={() => chooseAppointmentType(type)}>
                  {type.name}{' '}
                  {type.duration_minutes !== undefined && (
                    <span className="muted">({type.duration_minutes} min)</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
          <div className="step-actions">
            <button
              type="button"
              className="link"
              onClick={() => setStep(mode === 'date-first' ? 'department' : 'doctor')}
            >
              Back
            </button>
            <button type="button" className="link" onClick={startOver}>
              Main Menu
            </button>
          </div>
        </>
      )}

      {step === 'date' && appointmentType && mode === 'date-first' && department && (
        <>
          <h2>Choose a date</h2>
          <p className="muted">Showing every doctor with an opening -- pick a date to see who's available.</p>
          <DepartmentCalendar
            departmentId={department.id}
            appointmentTypeId={appointmentType.id}
            onSelectDate={chooseDateFirstDate}
          />
          <div className="step-actions">
            <button type="button" className="link" onClick={() => setStep('appointmentType')}>
              Back
            </button>
            <button type="button" className="link" onClick={startOver}>
              Main Menu
            </button>
          </div>
        </>
      )}

      {step === 'date' && doctor && appointmentType && mode !== 'date-first' && (
        <>
          <h2>Choose a date</h2>
          <Calendar
            doctorId={doctor.id}
            appointmentTypeId={appointmentType.id}
            departmentId={department?.id}
            onSelectDate={chooseDate}
          />
          <div className="step-actions">
            <button type="button" className="link" onClick={() => setStep('appointmentType')}>
              Back
            </button>
            <button type="button" className="link" onClick={startOver}>
              Main Menu
            </button>
          </div>
        </>
      )}

      {step === 'availableDoctors' && selectedDate && (
        <>
          <h2>Doctors available on {formatDate(selectedDate)}</h2>
          {availableDoctors.length === 0 ? (
            <p className="calendar-empty-state">
              No doctors have availability on this date. Please choose another date.
            </p>
          ) : (
            <ul className="option-list">
              {availableDoctors.map((doc) => (
                <DoctorCard
                  key={doc.id}
                  doctor={doc}
                  extra={
                    <span className="muted doctor-option-meta">
                      {doc.slots.length} {doc.slots.length === 1 ? 'slot' : 'slots'} available
                    </span>
                  }
                  onSelect={() => chooseAvailableDoctor(doc)}
                  onViewProfile={() => setViewingProfileDoctorId(doc.id)}
                />
              ))}
            </ul>
          )}
          <div className="step-actions">
            <button type="button" className="link" onClick={() => setStep('date')}>
              Back
            </button>
            <button type="button" className="link" onClick={startOver}>
              Main Menu
            </button>
          </div>
        </>
      )}

      {step === 'slot' && selectedDate && (
        <>
          <h2>Choose a time on {formatDate(selectedDate)}</h2>
          <SlotGrid slots={slots} onSelect={chooseSlot} />
          <div className="step-actions">
            <button type="button" className="link" onClick={backFromSlot}>
              Back
            </button>
            <button type="button" className="link" onClick={startOver}>
              Main Menu
            </button>
          </div>
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
          </dl>
          <button type="button" onClick={confirmBooking} disabled={busy}>
            {busy ? 'Booking…' : 'Confirm booking'}
          </button>
          <div className="step-actions">
            <button type="button" className="link" onClick={() => setStep('slot')}>
              Back
            </button>
            <button type="button" className="link" onClick={startOver}>
              Main Menu
            </button>
          </div>
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

      {viewingProfileDoctorId !== null && (
        <DoctorProfileModal
          doctorId={viewingProfileDoctorId}
          onClose={() => setViewingProfileDoctorId(null)}
        />
      )}
    </div>
  )
}
