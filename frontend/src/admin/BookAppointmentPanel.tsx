import { useEffect, useState } from 'react'
import { ArrowRight, CalendarBlank, CaretDown, CaretLeft, CaretRight, CheckCircle, MagnifyingGlass } from '@phosphor-icons/react'
import {
  ApiError,
  confirmAndCheckInAdmin,
  createAdminAppointment,
  getAppConfig,
  getSlotsForDate,
  listAllDoctors,
  listAppointmentTypesForDoctor,
  listPatients,
} from '../api'
import type { ArrivalActionResult, AppointmentType, BookingSource, Doctor, Patient, Slot } from '../types'
import { formatAvailability, formatDate, formatTime } from '../format'
import { isoDateToday } from './doctorSchedule'
import AvailabilityBadge from '../AvailabilityBadge'
import SlotGrid from '../SlotGrid'
import AdminCalendar from './AdminCalendar'
import PatientFormModal from './PatientFormModal'

// Now persisted on the appointment (migrations/0023) -- see types.ts's
// BookingSource for the ONLINE/PHONE/WALK_IN/STAFF_ASSISTED reasoning.
const BOOKING_SOURCES: { key: BookingSource; label: string }[] = [
  { key: 'ONLINE', label: 'Online' },
  { key: 'PHONE', label: 'Phone' },
  { key: 'WALK_IN', label: 'Walk-in' },
  { key: 'STAFF_ASSISTED', label: 'Staff-assisted' },
]

function addDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00`)
  d.setDate(d.getDate() + days)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// A dedicated page for the one thing it does: put a new appointment on
// the calendar for a patient who isn't booking it themselves (phone
// call, walk-in, etc.) -- kept separate from the Appointments section
// (which lists/filters/reschedules/cancels *existing* appointments) so
// the two nav items land somewhere visibly different instead of the
// same list with a form silently toggled open inside it.
export default function BookAppointmentPanel({ onViewAppointments }: { onViewAppointments: () => void }) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [doctorsLoading, setDoctorsLoading] = useState(true)
  const [patients, setPatients] = useState<Patient[]>([])
  const [patientsLoading, setPatientsLoading] = useState(true)
  const [timezoneLabel, setTimezoneLabel] = useState<string | null>(null)

  const [bookingSource, setBookingSource] = useState<BookingSource>('WALK_IN')

  const [patientSearch, setPatientSearch] = useState('')
  const [selectedPatient, setSelectedPatient] = useState<Patient | null>(null)
  const [showRegisterModal, setShowRegisterModal] = useState(false)

  const [doctorId, setDoctorId] = useState('')
  const [appointmentTypeId, setAppointmentTypeId] = useState('')
  const [types, setTypes] = useState<AppointmentType[]>([])

  const [date, setDate] = useState('')
  const [showCalendar, setShowCalendar] = useState(false)
  // Deliberately the SAME fetch (getSlotsForDate, the shared
  // availability_engine every other booking path in this app already
  // uses) that produces BOTH the displayed count and the displayed
  // slot buttons below -- slots.length is never computed separately
  // from what's rendered, so "12 slots available" always means
  // exactly 12 selectable buttons.
  const [slots, setSlots] = useState<Slot[]>([])
  const [totalSlots, setTotalSlots] = useState(0)
  const [slotsLoading, setSlotsLoading] = useState(false)
  const [slotsError, setSlotsError] = useState<string | null>(null)
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)

  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [justBooked, setJustBooked] = useState<{
    appointmentId: number
    doctorName: string
    patientName: string
    slot: Slot
    bookingSource: BookingSource
  } | null>(null)
  // The walk-in "Confirm & Check In" combined action's own state --
  // separate from the create-appointment busy/error above, since it's
  // a second, later action against an appointment that already exists.
  const [arrivalResult, setArrivalResult] = useState<ArrivalActionResult | null>(null)
  const [arrivalBusy, setArrivalBusy] = useState(false)
  const [arrivalError, setArrivalError] = useState<string | null>(null)

  useEffect(() => {
    listAllDoctors()
      .then(setDoctors)
      .catch(() => undefined)
      .finally(() => setDoctorsLoading(false))
    listPatients()
      .then(setPatients)
      .catch(() => undefined)
      .finally(() => setPatientsLoading(false))
    getAppConfig()
      .then((c) => setTimezoneLabel(c.default_timezone))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    setAppointmentTypeId('')
    setDate('')
    setShowCalendar(false)
    setSelectedSlot(null)
    if (!doctorId) {
      setTypes([])
      return
    }
    listAppointmentTypesForDoctor(Number(doctorId))
      .then(setTypes)
      .catch(() => setTypes([]))
  }, [doctorId])

  useEffect(() => {
    setDate('')
    setSelectedSlot(null)
  }, [appointmentTypeId])

  const selectedType = types.find((t) => String(t.id) === appointmentTypeId) ?? null
  const selectedDoctor = doctors.find((d) => String(d.id) === doctorId) ?? null

  useEffect(() => {
    if (!date || !selectedType || !doctorId) {
      setSlots([])
      setTotalSlots(0)
      return
    }
    let cancelled = false
    setSlotsLoading(true)
    setSlotsError(null)
    setSelectedSlot(null)
    getSlotsForDate(Number(doctorId), selectedType.id, date)
      .then((result) => {
        if (cancelled) return
        setSlots(result.slots)
        setTotalSlots(result.total_slots)
      })
      .catch((err) => {
        if (cancelled) return
        setSlotsError(err instanceof ApiError ? err.message : 'Unable to load available slots. Please try again.')
        setSlots([])
        setTotalSlots(0)
      })
      .finally(() => {
        if (!cancelled) setSlotsLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectedType is derived from types+appointmentTypeId every render; .id is the only part that should retrigger this fetch
  }, [date, doctorId, selectedType?.id])

  const searchNeedle = patientSearch.trim().toLowerCase()
  const patientResults = searchNeedle
    ? patients
        .filter(
          (p) =>
            p.name.toLowerCase().includes(searchNeedle) ||
            p.whatsapp_number.toLowerCase().includes(searchNeedle) ||
            String(p.id).includes(searchNeedle),
        )
        .slice(0, 8)
    : []

  function selectPatient(p: Patient) {
    setSelectedPatient(p)
    setPatientSearch('')
  }

  function handlePatientRegistered(p: Patient) {
    setPatients((prev) => [...prev, p])
    selectPatient(p)
  }

  // What's still missing before this can be submitted, in the order the
  // page's own steps are numbered -- null once everything required
  // (including an actual time slot, not just a date) is in place. Drives
  // both the Book button's disabled state and the helper text below the
  // review card, so the two can never drift out of sync.
  function missingSelectionMessage(): string | null {
    if (!selectedPatient) return 'Search for or register a patient to continue.'
    if (!doctorId) return 'Select a doctor to continue.'
    if (!appointmentTypeId) return 'Select an appointment type to continue.'
    if (!selectedSlot) return 'Pick an available date and time slot to continue.'
    return null
  }

  const missingSelection = missingSelectionMessage()
  const canSubmit = !busy && missingSelection === null

  function resetForm() {
    setSelectedPatient(null)
    setPatientSearch('')
    setDoctorId('')
    setAppointmentTypeId('')
    setDate('')
    setShowCalendar(false)
    setSelectedSlot(null)
    setBookingSource('WALK_IN')
  }

  async function handleSubmit() {
    if (!selectedPatient || !doctorId || !appointmentTypeId || !selectedSlot || !selectedDoctor) return
    setError(null)
    setBusy(true)
    try {
      const created = await createAdminAppointment(
        Number(doctorId),
        selectedPatient.id,
        Number(appointmentTypeId),
        selectedSlot.start_at,
        bookingSource,
      )
      setJustBooked({
        appointmentId: created.id,
        doctorName: selectedDoctor.name,
        patientName: selectedPatient.name,
        slot: selectedSlot,
        bookingSource,
      })
      setArrivalResult(null)
      setArrivalError(null)
      resetForm()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'That slot is no longer available. Please select another slot.',
      )
    } finally {
      setBusy(false)
    }
  }

  // The walk-in combined action -- composes confirm/visit/arrive
  // server-side (confirm_and_check_in_service). Never assumes the
  // result reached CHECKED_IN: arrival_kind says which of the two
  // branches actually happened, and the success screen below reflects
  // whichever one it was rather than always claiming "checked in."
  async function handleConfirmAndCheckIn() {
    if (!justBooked) return
    setArrivalBusy(true)
    setArrivalError(null)
    try {
      const result = await confirmAndCheckInAdmin(justBooked.appointmentId)
      setArrivalResult(result)
    } catch (err) {
      setArrivalError(err instanceof ApiError ? err.message : 'Could not confirm and check in this appointment.')
    } finally {
      setArrivalBusy(false)
    }
  }

  const today = isoDateToday()

  function jumpToDate(target: string) {
    setDate(target)
    setShowCalendar(false)
  }

  function stepDay(delta: number) {
    const next = addDays(date || today, delta)
    if (next < today) return
    setDate(next)
  }

  if (justBooked) {
    return (
      <section>
        <h2>Book Appointment</h2>
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <CheckCircle size={28} weight="light" />
          </span>
          <p>
            Booked {justBooked.patientName} with {justBooked.doctorName} at {formatTime(justBooked.slot.start_at)} on{' '}
            {formatDate(justBooked.slot.start_at)}.
          </p>

          {/* Walk-in only -- the patient is physically present, so
              reception can confirm and check them in immediately
              instead of finding this same appointment again on the
              Appointments page. Never claims the patient is queued:
              arrival_kind decides which message shows below, and
              payment/waiver (unchanged) still gates the actual queue
              token either way. */}
          {justBooked.bookingSource === 'WALK_IN' && !arrivalResult && (
            <div style={{ marginTop: 8 }}>
              {arrivalError && <p className="error">{arrivalError}</p>}
              <button type="button" className="btn btn-sm" disabled={arrivalBusy} onClick={handleConfirmAndCheckIn}>
                {arrivalBusy ? 'Confirming…' : 'Confirm & Check In'}
              </button>
            </div>
          )}

          {arrivalResult && (
            <p className="muted" style={{ marginTop: 4 }}>
              {arrivalResult.arrival_kind === 'checked_in'
                ? 'Checked in -- go to Appointments to collect payment or waive the fee and add them to the queue.'
                : `Confirmed -- this patient will be eligible to check in once their ${formatTime(justBooked.slot.start_at)} appointment time arrives.`}
            </p>
          )}

          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginTop: 8 }}>
            <button
              type="button"
              className="btn-secondary btn btn-sm"
              onClick={() => {
                setJustBooked(null)
                setArrivalResult(null)
                setArrivalError(null)
              }}
            >
              Book another
            </button>
            <button type="button" className="btn-secondary btn btn-sm" onClick={onViewAppointments}>
              View appointments
            </button>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section className="book-appointment-page">
      <div className="admin-content-header">
        <div>
          <h2>Book Appointment</h2>
          <p className="muted">Schedule an appointment for a patient by phone, walk-in, or on their behalf.</p>
        </div>
        <div className="booking-source-picker">
          <span className="field-label">Booking source</span>
          <div className="booking-source-pills" role="radiogroup" aria-label="Booking source">
            {BOOKING_SOURCES.map((s) => (
              <button
                key={s.key}
                type="button"
                role="radio"
                aria-checked={s.key === bookingSource}
                className={s.key === bookingSource ? 'date-scope-pill active' : 'date-scope-pill'}
                onClick={() => setBookingSource(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="book-appointment-layout">
        <div className="book-appointment-main">
          {/* Step 1: Patient */}
          <div className="book-step-card">
            <div className="book-step-heading">
              <span className={`book-step-number${selectedPatient ? ' done' : ' current'}`} aria-hidden="true">
                1
              </span>
              <div>
                <h3>Patient</h3>
                <p className="muted">Search for an existing patient or register a new one.</p>
              </div>
            </div>
            <div className="book-step1-body">
              <div className="book-patient-search">
                <label className="filter-bar-search-input book-patient-search-input">
                  <MagnifyingGlass size={16} aria-hidden="true" />
                  <input
                    type="search"
                    placeholder="Search by name, phone number or patient ID…"
                    value={patientSearch}
                    onChange={(e) => setPatientSearch(e.target.value)}
                    aria-label="Search by name, phone number or patient ID"
                  />
                </label>

                {searchNeedle && patientsLoading && (
                  <div className="book-patient-no-results">
                    <span className="spinner" aria-hidden="true" />
                    <p className="muted">Loading patients…</p>
                  </div>
                )}

                {searchNeedle && !patientsLoading &&
                  (patientResults.length > 0 ? (
                    <ul className="book-patient-results">
                      {patientResults.map((p) => (
                        <li key={p.id}>
                          <button type="button" className="book-patient-result" onClick={() => selectPatient(p)}>
                            <span className="book-patient-avatar" aria-hidden="true">
                              {p.name.slice(0, 2).toUpperCase()}
                            </span>
                            <span className="book-patient-result-info">
                              <strong>{p.name}</strong>
                              <span className="muted">
                                Patient ID: {p.id} · {p.whatsapp_number}
                              </span>
                            </span>
                            <span className="book-patient-result-hint" aria-hidden="true">
                              Select <ArrowRight size={13} weight="bold" />
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="book-patient-no-results">
                      <p>No patient found</p>
                      <p className="muted">Try another name, phone number, or patient ID.</p>
                    </div>
                  ))}

                <button type="button" className="btn-secondary btn btn-sm book-register-btn" onClick={() => setShowRegisterModal(true)}>
                  + Register new patient
                </button>
              </div>

              {selectedPatient && (
                <div className="book-selected-patient-card">
                  <div className="book-selected-patient-header">
                    <span>
                      <CheckCircle size={14} weight="bold" aria-hidden="true" /> Patient selected
                    </span>
                    <button type="button" className="link" onClick={() => setSelectedPatient(null)}>
                      Change
                    </button>
                  </div>
                  <div className="book-selected-patient-body">
                    <span className="book-patient-avatar" aria-hidden="true">
                      {selectedPatient.name.slice(0, 2).toUpperCase()}
                    </span>
                    <div>
                      <strong>{selectedPatient.name}</strong>
                      <div className="muted">Patient ID: {selectedPatient.id}</div>
                      <div className="muted">{selectedPatient.whatsapp_number}</div>
                    </div>
                  </div>
                  {selectedPatient.patient_type && (
                    <div className="book-selected-patient-type">
                      {selectedPatient.patient_type === 'recurring' ? 'Existing patient' : 'First-time patient'}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Step 2: Appointment Details */}
          <div className={`book-step-card${!selectedPatient ? ' book-step-disabled' : ''}`}>
            <div className="book-step-heading">
              <span className={`book-step-number${selectedType ? ' done' : selectedPatient ? ' current' : ''}`} aria-hidden="true">
                2
              </span>
              <div>
                <h3>Appointment Details</h3>
                <p className="muted">Select doctor and appointment type.</p>
              </div>
            </div>
            <div className="inline-form wrap" style={{ marginTop: 0 }}>
              <label className="inline-label">
                Doctor
                <select
                  value={doctorId}
                  onChange={(e) => setDoctorId(e.target.value)}
                  disabled={!selectedPatient || doctorsLoading}
                  required
                >
                  <option value="">{doctorsLoading ? 'Loading doctors…' : 'Choose…'}</option>
                  {doctors.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                      {d.specialization ? ` — ${d.specialization}` : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label className="inline-label">
                Appointment type
                <select
                  value={appointmentTypeId}
                  onChange={(e) => setAppointmentTypeId(e.target.value)}
                  disabled={!doctorId}
                  required
                >
                  <option value="">Choose…</option>
                  {types.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} ({t.duration_minutes} min)
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          {/* Step 3: Date & Time */}
          <div className={`book-step-card${!selectedType ? ' book-step-disabled' : ''}`}>
            <div className="book-step-heading">
              <span className={`book-step-number${selectedSlot ? ' done' : selectedType ? ' current' : ''}`} aria-hidden="true">
                3
              </span>
              <div>
                <h3>Date &amp; Time</h3>
                <p className="muted">Select a date and available time slot.</p>
              </div>
            </div>

            <div className="book-date-nav">
              <button type="button" className="icon-btn" aria-label="Previous day" disabled={!selectedType} onClick={() => stepDay(-1)}>
                <CaretLeft size={16} />
              </button>
              <button
                type="button"
                className="book-date-nav-label"
                disabled={!selectedType}
                onClick={() => setShowCalendar((v) => !v)}
                aria-expanded={showCalendar}
              >
                <CalendarBlank size={15} weight="bold" aria-hidden="true" />
                {date ? formatDate(date) : 'Select a date'}
                <CaretDown size={12} weight="bold" aria-hidden="true" />
              </button>
              <button type="button" className="icon-btn" aria-label="Next day" disabled={!selectedType} onClick={() => stepDay(1)}>
                <CaretRight size={16} />
              </button>
            </div>

            <div className="date-scope-row">
              <button type="button" className={date === today ? 'date-scope-pill active' : 'date-scope-pill'} disabled={!selectedType} onClick={() => jumpToDate(today)}>
                Today
              </button>
              <button type="button" className={date === addDays(today, 1) ? 'date-scope-pill active' : 'date-scope-pill'} disabled={!selectedType} onClick={() => jumpToDate(addDays(today, 1))}>
                Tomorrow
              </button>
              {/* This Week/This Month/Custom Range all open the same
                  month calendar below rather than setting a date RANGE:
                  a booking is for exactly one date, so "This Week"/
                  "This Month" are just faster ways to get to the
                  calendar (which already lets you go to any day, this
                  month or later) rather than a second, distinct kind of
                  filter. */}
              <button type="button" className="date-scope-pill" disabled={!selectedType} onClick={() => setShowCalendar(true)}>
                This Week
              </button>
              <button type="button" className="date-scope-pill" disabled={!selectedType} onClick={() => setShowCalendar(true)}>
                This Month
              </button>
              <button type="button" className="date-scope-pill" disabled={!selectedType} onClick={() => setShowCalendar(true)}>
                <CalendarBlank size={14} weight="bold" aria-hidden="true" /> Custom Range
              </button>
            </div>

            {showCalendar && selectedType && (
              <AdminCalendar doctorId={Number(doctorId)} appointmentTypeId={selectedType.id} onSelectDate={jumpToDate} selectedDate={date || null} />
            )}

            {date && selectedType && (
              <>
                <div className="book-availability-row">
                  {!slotsLoading && <AvailabilityBadge availability={formatAvailability(slots.length, totalSlots)} />}
                  {timezoneLabel && <span className="muted">Doctor's time zone: {timezoneLabel}</span>}
                </div>
                {slotsError && <p className="error">{slotsError}</p>}
                <SlotGrid
                  slots={slots}
                  selectedSlot={selectedSlot}
                  onSelect={setSelectedSlot}
                  loading={slotsLoading}
                  // total_slots (from the same availability response
                  // slots.length itself comes from) is 0 only when the
                  // doctor's schedule generates no candidate windows at
                  // all that day -- distinct from "generated some, but
                  // every one is already booked/blocked", which is what
                  // a non-zero total_slots with an empty slots list means.
                  emptyMessage={
                    totalSlots === 0
                      ? 'Doctor is not available on this date.'
                      : 'No slots available for this date -- try another date or doctor.'
                  }
                />
              </>
            )}
          </div>
        </div>

        {/* Step 4: Review */}
        <div className="book-appointment-side">
          <div className="book-review-card">
            <div className="book-step-heading">
              <span className={`book-step-number${canSubmit ? ' done' : ''}`} aria-hidden="true">
                4
              </span>
              <div>
                <h3>Review Appointment</h3>
                <p className="muted">Confirm the details before booking.</p>
              </div>
            </div>

            <dl className="book-review-list">
              <div>
                <dt>Patient</dt>
                <dd>
                  {selectedPatient ? (
                    <>
                      <strong>{selectedPatient.name}</strong>
                      <span className="muted">
                        Patient ID: {selectedPatient.id} · {selectedPatient.whatsapp_number}
                      </span>
                    </>
                  ) : (
                    <span className="muted">Not selected</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Doctor</dt>
                <dd>
                  {selectedDoctor ? (
                    <>
                      <strong>{selectedDoctor.name}</strong>
                      {selectedDoctor.specialization && <span className="muted">{selectedDoctor.specialization}</span>}
                    </>
                  ) : (
                    <span className="muted">Not selected</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Appointment type</dt>
                <dd>
                  {selectedType ? (
                    <>
                      <strong>{selectedType.name}</strong>
                      <span className="muted">{selectedType.duration_minutes} minutes</span>
                    </>
                  ) : (
                    <span className="muted">Not selected</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Date</dt>
                <dd>{date ? <strong>{formatDate(date)}</strong> : <span className="muted">Not selected</span>}</dd>
              </div>
              <div>
                <dt>Time</dt>
                <dd>
                  {selectedSlot ? (
                    <strong>
                      {formatTime(selectedSlot.start_at)} – {formatTime(selectedSlot.end_at)}
                    </strong>
                  ) : (
                    <span className="muted">Not selected</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Booking source</dt>
                <dd>
                  <strong>{BOOKING_SOURCES.find((s) => s.key === bookingSource)?.label}</strong>
                </dd>
              </div>
            </dl>

            {!busy && (
              <p className="muted book-review-hint" aria-live="polite">
                {missingSelection ?? 'Ready to book -- review the details above, then confirm.'}
              </p>
            )}

            <button type="button" className="btn book-review-cta" disabled={!canSubmit} onClick={handleSubmit}>
              <CalendarBlank size={16} weight="bold" aria-hidden="true" /> {busy ? 'Booking…' : 'Book Appointment'}
            </button>
            <button type="button" className="btn-secondary btn" onClick={resetForm}>
              Cancel
            </button>
          </div>
        </div>
      </div>

      {showRegisterModal && (
        <PatientFormModal
          mode="create"
          title="Register new patient"
          onClose={() => setShowRegisterModal(false)}
          onSaved={handlePatientRegistered}
        />
      )}
    </section>
  )
}
