import { useEffect, useState } from 'react'
import { ArrowRight, CalendarBlank, CaretDown, CaretLeft, CaretRight, CheckCircle, MagnifyingGlass } from '@phosphor-icons/react'
import {
  ApiError,
  confirmAndCheckInAdmin,
  createAdminAppointment,
  getAppConfig,
  getDoctorDepartments,
  getSlotsForDate,
  listAllDoctors,
  listAppointmentTypesForDoctor,
  searchPatientsAdmin,
  settleFreeVisitAdmin,
} from '../api'
import type { ArrivalActionResult, AppointmentType, BookingSource, Doctor, PaymentActionResult, Patient, Slot } from '../types'
import { formatAvailability, formatDate, formatPreciseAge, formatTime } from '../format'
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

// The patient search box below accepts free text (name, mobile number,
// or UHID all go through the same field) -- there's no PhoneInput-style
// "+91" prefix/10-digit cap to stop someone from fat-fingering a phone
// number here the way there is on the registration form. A malformed
// attempt (a stray leading sign, too many/too few digits) will never
// substring-match a real patients.whatsapp_number (always exactly
// "+91" + 10 digits, per app/utils/phone.py's normalization), so it
// silently falls through to the generic "No existing patient found" --
// indistinguishable, from the receptionist's side, from the patient
// genuinely not being on file. That's the failure mode this exists to
// catch: a query that's clearly an attempted phone number (mostly/only
// digits, with an optional leading +/-) but isn't shaped like a valid
// one, so the empty-results state can say so instead of quietly
// inviting a duplicate registration.
function looksLikeMistypedPhoneNumber(query: string): boolean {
  const stripped = query.replace(/[\s\-()]/g, '')
  const match = stripped.match(/^[+-]?(\d+)$/)
  if (!match) return false
  const digits = match[1]
  // Too short to plausibly be a finished phone number yet (still
  // mid-typed) -- not flagged, so every few-digit keystroke on the way
  // to a real number doesn't flash an error.
  if (digits.length < 7) return false
  const isValidShape = digits.length === 10 || (digits.length === 12 && digits.startsWith('91'))
  return !isValidShape
}

// A dedicated page for the one thing it does: put a new appointment on
// the calendar for a patient who isn't booking it themselves (phone
// call, walk-in, etc.) -- kept separate from the Appointments section
// (which lists/filters/reschedules/cancels *existing* appointments) so
// the two nav items land somewhere visibly different instead of the
// same list with a form silently toggled open inside it.
export default function BookAppointmentPanel({
  onViewAppointments,
  onGoToQueue,
  onGoToPatients,
  autoOpenRegister,
}: {
  onViewAppointments: () => void
  // Success screen's "View Queue" action (point 10) -- same doctor-
  // scoped queue hand-off AdminApp.tsx's goToQueueForDoctor already
  // gives DoctorsPanel/AppointmentsPanel. Optional: only shown once a
  // queue token actually exists, so a caller that never needs it
  // (there is currently only one) can omit it.
  onGoToQueue?: (doctorId: number) => void
  // Success screen's "View Patient" action -- routes to the Patients
  // directory (no per-patient deep link exists yet in this app; adding
  // one is outside this redesign's scope).
  onGoToPatients?: () => void
  // OPD Today's "New OPD Visit > Register New Patient" entry (see
  // AppointmentsPanel.tsx) lands here instead of opening
  // PatientFormModal directly -- point 1 of the OPD Patient Search &
  // Registration redesign: registration is never the starting point of
  // an OPD visit, the receptionist always searches first. This still
  // opens the register modal for them (skipping the extra click), but
  // only after landing on the search step, matching every other patient-
  // registration entry point (Walk-in/Book Appointment's own "+
  // Register new patient" button) instead of bypassing it.
  autoOpenRegister?: boolean
}) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [doctorsLoading, setDoctorsLoading] = useState(true)
  const [timezoneLabel, setTimezoneLabel] = useState<string | null>(null)

  const [bookingSource, setBookingSource] = useState<BookingSource>('WALK_IN')

  const [patientSearch, setPatientSearch] = useState('')
  const [patientResults, setPatientResults] = useState<Patient[]>([])
  const [patientResultsLoading, setPatientResultsLoading] = useState(false)
  const [patientSearchError, setPatientSearchError] = useState<string | null>(null)
  const [selectedPatient, setSelectedPatient] = useState<Patient | null>(null)
  const [showRegisterModal, setShowRegisterModal] = useState(Boolean(autoOpenRegister))
  // Opens PatientFormModal in edit mode for the already-selected patient
  // -- "verify/update details, then continue" without losing the
  // selection (unlike "Change", which clears it back to search).
  const [showEditModal, setShowEditModal] = useState(false)

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
    doctorId: number
    doctorName: string
    patientId: number
    patientName: string
    patientUhid: string
    appointmentTypeName: string
    // Snapshot of selectedType.consultation_fee at booking time -- the
    // step 2 selection is cleared by resetForm() right after this is
    // set, so the success screen (and the auto-settle-free-visit call
    // below) need their own copy rather than reading selectedType.
    consultationFee: number
    slot: Slot
    bookingSource: BookingSource
  } | null>(null)
  // The walk-in "Confirm & Check In" combined action's own state --
  // separate from the create-appointment busy/error above, since it's
  // a second, later action against an appointment that already exists.
  const [arrivalResult, setArrivalResult] = useState<ArrivalActionResult | null>(null)
  const [arrivalBusy, setArrivalBusy] = useState(false)
  const [arrivalError, setArrivalError] = useState<string | null>(null)
  // Payment must be configurable, not hardcoded mandatory (point 9): a
  // walk-in whose selected appointment type has no consultation fee
  // configured settles automatically the moment check-in succeeds
  // (settle_free_visit_service), instead of forcing staff through a
  // manual Collect Payment/Waive Charge step for a genuinely free
  // visit. A nonzero fee is entirely unaffected -- that patient still
  // goes through the ordinary payment flow on the Appointments page,
  // same as before this redesign.
  const [paymentSettleResult, setPaymentSettleResult] = useState<PaymentActionResult | null>(null)
  const [paymentSettleError, setPaymentSettleError] = useState<string | null>(null)
  // The success screen's "Department" line (point 10) -- fetched once
  // justBooked is set, same "earliest-assigned = primary" convention
  // DoctorWorkspace.tsx already uses, since an appointment itself
  // carries no department (a doctor can offer the same appointment
  // type across more than one).
  const [justBookedDepartment, setJustBookedDepartment] = useState<string | null>(null)

  useEffect(() => {
    listAllDoctors()
      .then(setDoctors)
      .catch(() => undefined)
      .finally(() => setDoctorsLoading(false))
    getAppConfig()
      .then((c) => setTimezoneLabel(c.default_timezone))
      .catch(() => undefined)
  }, [])

  // Backend-driven search (GET /patients/search), not a client-side
  // filter over the whole registry -- point 2/7 of the redesign: the
  // receptionist must be able to positively identify (or rule out) an
  // existing patient by name, mobile number, or UHID before ever
  // reaching the registration form, and that has to scale past however
  // many patients this clinic has on file. Debounced (300ms) so every
  // keystroke doesn't fire its own request; a stale response for a
  // since-changed query is dropped via the `cancelled` guard, same
  // pattern the slot-availability fetch above already uses.
  useEffect(() => {
    const needle = patientSearch.trim()
    if (!needle) {
      setPatientResults([])
      setPatientResultsLoading(false)
      setPatientSearchError(null)
      return
    }
    let cancelled = false
    setPatientResultsLoading(true)
    setPatientSearchError(null)
    const timer = setTimeout(() => {
      searchPatientsAdmin(needle)
        .then((results) => {
          if (cancelled) return
          setPatientResults(results)
        })
        .catch((err) => {
          if (cancelled) return
          setPatientSearchError(err instanceof ApiError ? err.message : 'Unable to search patients. Please try again.')
          setPatientResults([])
        })
        .finally(() => {
          if (!cancelled) setPatientResultsLoading(false)
        })
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [patientSearch])

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

  // Defaults Step 3 to today the moment an appointment type is picked,
  // rather than leaving `date` empty until staff explicitly clicks
  // Today/a calendar day -- the slot fetch below only runs once `date`
  // is set, so without this, step 3's availability never appears on
  // its own. Resets back to '' when appointmentTypeId is cleared (e.g.
  // the doctor-change effect above clearing it), so the date field
  // doesn't carry a stale selection with no type chosen.
  useEffect(() => {
    setDate(appointmentTypeId ? isoDateToday() : '')
    setSelectedSlot(null)
  }, [appointmentTypeId])

  useEffect(() => {
    if (!justBooked) {
      setJustBookedDepartment(null)
      return
    }
    let cancelled = false
    getDoctorDepartments(justBooked.doctorId)
      .then((assignments) => {
        if (cancelled || assignments.length === 0) return
        const earliest = assignments.reduce((min, d) => (d.assigned_at < min.assigned_at ? d : min), assignments[0])
        setJustBookedDepartment(earliest.name)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [justBooked])

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

  const searchNeedle = patientSearch.trim()

  function selectPatient(p: Patient) {
    setSelectedPatient(p)
    setPatientSearch('')
    setPatientResults([])
  }

  function handlePatientRegistered(p: Patient) {
    selectPatient(p)
  }

  // Updates the selected patient in place -- the appointment-in-
  // progress (doctor/type/date/slot already chosen) is untouched.
  function handlePatientEdited(p: Patient) {
    setSelectedPatient(p)
  }

  const isWalkIn = bookingSource === 'WALK_IN'

  // What's still missing before this can be submitted, in the order the
  // page's own steps are numbered -- null once everything required
  // (including an actual time slot, not just a date) is in place. Drives
  // both the Book button's disabled state and the helper text below the
  // review card, so the two can never drift out of sync.
  function missingSelectionMessage(): string | null {
    if (!selectedPatient) return 'Search for or register a patient to continue.'
    // Checks selectedDoctor, not just doctorId -- handleSubmit below
    // requires selectedDoctor too (it reads selectedDoctor.name for the
    // confirmation screen), so if doctorId ever doesn't resolve to an
    // entry in `doctors` (e.g. this component held a stale selection
    // across a doctors-list refresh), the Book button must stay
    // disabled rather than appear clickable and silently no-op.
    if (!doctorId || !selectedDoctor) return 'Select a doctor to continue.'
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
    // The Book button is disabled whenever missingSelectionMessage() is
    // non-null (same fields checked there), so this should be
    // unreachable in normal use -- kept as a guard, not silently, so a
    // click that somehow gets through with an incomplete selection
    // surfaces an actual error instead of doing nothing.
    if (!selectedPatient || !doctorId || !appointmentTypeId || !selectedSlot || !selectedDoctor || !selectedType) {
      setError('Some required fields are missing. Please review your selection and try again.')
      return
    }
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
        doctorId: Number(doctorId),
        patientId: selectedPatient.id,
        patientUhid: selectedPatient.uhid,
        appointmentTypeName: selectedType.name,
        consultationFee: selectedType.consultation_fee,
        appointmentId: created.id,
        doctorName: selectedDoctor.name,
        patientName: selectedPatient.name,
        slot: selectedSlot,
        bookingSource,
      })
      setArrivalResult(null)
      setArrivalError(null)
      setPaymentSettleResult(null)
      setPaymentSettleError(null)
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
  //
  // When it does reach CHECKED_IN and the selected appointment type has
  // no consultation fee configured, this also fires settle-free-visit
  // right away (point 9: payment is configurable, not hardcoded
  // mandatory) -- the receptionist never sees a Collect Payment/Waive
  // Charge step for a visit that was never going to charge anything. A
  // nonzero fee never reaches this branch at all; that patient's
  // payment step still happens on the Appointments page, unchanged.
  async function handleConfirmAndCheckIn() {
    if (!justBooked) return
    setArrivalBusy(true)
    setArrivalError(null)
    try {
      const result = await confirmAndCheckInAdmin(justBooked.appointmentId)
      setArrivalResult(result)
      if (result.arrival_kind === 'checked_in' && justBooked.consultationFee === 0) {
        try {
          const settled = await settleFreeVisitAdmin(justBooked.appointmentId)
          setPaymentSettleResult(settled)
        } catch (err) {
          setPaymentSettleError(err instanceof ApiError ? err.message : 'Could not settle this free visit automatically.')
        }
      }
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
        <h2>OPD Visit Created Successfully</h2>
        <div className="state-block empty opd-success">
          <span className="state-icon" aria-hidden="true">
            <CheckCircle size={28} weight="light" />
          </span>

          <dl className="opd-success-details">
            <div>
              <dt>Patient</dt>
              <dd>{justBooked.patientName}</dd>
            </div>
            <div>
              <dt>UHID</dt>
              <dd>{justBooked.patientUhid}</dd>
            </div>
            {paymentSettleResult?.token_number != null && (
              <div>
                <dt>Token</dt>
                <dd>
                  <strong>#{paymentSettleResult.token_number}</strong>
                </dd>
              </div>
            )}
            {justBookedDepartment && (
              <div>
                <dt>Department</dt>
                <dd>{justBookedDepartment}</dd>
              </div>
            )}
            <div>
              <dt>Doctor</dt>
              <dd>{justBooked.doctorName}</dd>
            </div>
            <div>
              <dt>Appointment type</dt>
              <dd>{justBooked.appointmentTypeName}</dd>
            </div>
            <div>
              <dt>Date &amp; Time</dt>
              <dd>
                {formatDate(justBooked.slot.start_at)} · {formatTime(justBooked.slot.start_at)}
              </dd>
            </div>
          </dl>

          {/* Walk-in only -- the patient is physically present, so
              reception can confirm and check them in immediately
              instead of finding this same appointment again on the
              Appointments page. Never claims the patient is queued:
              arrival_kind decides which message shows below. A
              configured (nonzero) fee still routes through the
              ordinary Collect Payment/Waive Charge flow there --
              this only fast-paths the genuinely free case. */}
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
              {arrivalResult.arrival_kind !== 'checked_in'
                ? `Confirmed -- this patient will be eligible to check in once their ${formatTime(justBooked.slot.start_at)} appointment time arrives.`
                : paymentSettleError
                  ? paymentSettleError
                  : paymentSettleResult?.token_number != null
                    ? 'No payment required for this visit -- already in the queue.'
                    : 'Checked in -- go to Appointments to collect payment or waive the fee and add them to the queue.'}
            </p>
          )}

          <div className="opd-success-actions">
            {paymentSettleResult?.token_number != null && onGoToQueue && (
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => onGoToQueue(justBooked.doctorId)}>
                View Queue
              </button>
            )}
            {onGoToPatients && (
              <button type="button" className="btn-secondary btn btn-sm" onClick={onGoToPatients}>
                View Patient
              </button>
            )}
            {paymentSettleResult?.token_number != null && (
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => window.print()}>
                Print Token
              </button>
            )}
            <button type="button" className="btn-secondary btn btn-sm" onClick={onViewAppointments}>
              View Appointments
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                setJustBooked(null)
                setArrivalResult(null)
                setArrivalError(null)
                setPaymentSettleResult(null)
                setPaymentSettleError(null)
              }}
            >
              Create Another Visit
            </button>
          </div>

          {/* master spec section 54's gap #6: "Print Token" used to
              call window.print() with no print CSS scoping it, so it
              printed the entire visible page (sidebar included) rather
              than a token slip. print-area + print-only (styles.css)
              give it its own compact, print-only layout instead. */}
          {paymentSettleResult?.token_number != null && (
            <div className="print-area print-only token-slip">
              <h3>Token #{paymentSettleResult.token_number}</h3>
              <p>
                {justBooked.patientName} ({justBooked.patientUhid})
              </p>
              <p>
                {justBookedDepartment ? `${justBookedDepartment} · ` : ''}
                {justBooked.doctorName}
              </p>
              <p>{justBooked.appointmentTypeName}</p>
              <p>
                {formatDate(justBooked.slot.start_at)} · {formatTime(justBooked.slot.start_at)}
              </p>
            </div>
          )}
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
                <h3>{isWalkIn ? 'Walk-in patient' : 'Patient'}</h3>
                <p className="muted">
                  {isWalkIn
                    ? 'Search for the patient who has arrived, or register them now.'
                    : 'Search for an existing patient or register a new one.'}
                </p>
              </div>
            </div>
            <div className="book-step1-body">
              <div className="book-patient-search">
                <label className="filter-bar-search-input book-patient-search-input">
                  <MagnifyingGlass size={16} aria-hidden="true" />
                  <input
                    type="search"
                    placeholder="Search by name, mobile number, or UHID…"
                    value={patientSearch}
                    onChange={(e) => setPatientSearch(e.target.value)}
                    aria-label="Search by name, mobile number, or UHID"
                  />
                </label>

                {searchNeedle && patientResultsLoading && (
                  <div className="book-patient-no-results">
                    <span className="spinner" aria-hidden="true" />
                    <p className="muted">Searching…</p>
                  </div>
                )}

                {searchNeedle && !patientResultsLoading && patientSearchError && (
                  <p className="error">{patientSearchError}</p>
                )}

                {searchNeedle && !patientResultsLoading && !patientSearchError &&
                  (patientResults.length > 0 ? (
                    <>
                      {/* Same list whether there's one match or several
                          (points 2/7 of the redesign) -- one card reads
                          as "Existing patient found", several as
                          "Multiple patients found. Select the correct
                          patient." Each row carries enough to tell two
                          same-named patients apart: UHID, DOB + age,
                          gender, mobile number. */}
                      <p className="book-patient-results-heading">
                        {patientResults.length === 1
                          ? 'Existing patient found'
                          : `${patientResults.length} patients found — select the correct one`}
                      </p>
                      <ul className="book-patient-results">
                        {patientResults.map((p) => {
                          const age = p.date_of_birth ? formatPreciseAge(p.date_of_birth) : null
                          return (
                            <li key={p.id}>
                              <button type="button" className="book-patient-result" onClick={() => selectPatient(p)}>
                                <span className="book-patient-avatar" aria-hidden="true">
                                  {p.name.slice(0, 2).toUpperCase()}
                                </span>
                                <span className="book-patient-result-info">
                                  <strong>{p.name}</strong>
                                  <span className="muted">
                                    {p.uhid}
                                    {p.date_of_birth ? ` · ${formatDate(p.date_of_birth)}` : ''}
                                    {age ? ` · ${age}` : ''}
                                    {p.gender ? ` · ${p.gender.charAt(0)}${p.gender.slice(1).toLowerCase()}` : ''}
                                  </span>
                                  <span className="muted">{p.whatsapp_number}</span>
                                </span>
                                <span className="book-patient-result-hint" aria-hidden="true">
                                  Select <ArrowRight size={13} weight="bold" />
                                </span>
                              </button>
                            </li>
                          )
                        })}
                      </ul>
                    </>
                  ) : looksLikeMistypedPhoneNumber(searchNeedle) ? (
                    <div className="book-patient-no-results">
                      <p>That doesn't look like a valid mobile number</p>
                      <p className="muted">
                        Expected a 10-digit Indian mobile number, optionally with a +91 country
                        code. Double-check what was typed before registering a new patient.
                      </p>
                    </div>
                  ) : (
                    <div className="book-patient-no-results">
                      <p>No existing patient found</p>
                      <p className="muted">{searchNeedle}</p>
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
                    <span className="book-selected-patient-actions">
                      <button type="button" className="link" onClick={() => setShowEditModal(true)}>
                        Edit
                      </button>
                      <button type="button" className="link" onClick={() => setSelectedPatient(null)}>
                        Change
                      </button>
                    </span>
                  </div>
                  <div className="book-selected-patient-body">
                    <span className="book-patient-avatar" aria-hidden="true">
                      {selectedPatient.name.slice(0, 2).toUpperCase()}
                    </span>
                    <div>
                      <strong>{selectedPatient.name}</strong>
                      <div className="muted">
                        {selectedPatient.uhid}
                        {selectedPatient.date_of_birth ? ` · ${formatDate(selectedPatient.date_of_birth)}` : ''}
                        {selectedPatient.gender
                          ? ` · ${selectedPatient.gender.charAt(0)}${selectedPatient.gender.slice(1).toLowerCase()}`
                          : ''}
                      </div>
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
                        {selectedPatient.uhid} · {selectedPatient.whatsapp_number}
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
              {/* Payment is configurable per appointment type, never
                  hardcoded mandatory (point 9) -- this reads the same
                  consultation_fee the check-in step itself decides
                  auto-settlement from, so what's shown here is never
                  out of sync with what actually happens after booking. */}
              <div>
                <dt>Payment</dt>
                <dd>
                  {selectedType ? (
                    selectedType.consultation_fee === 0 ? (
                      <span className="muted">No payment required — free visit</span>
                    ) : (
                      <strong>₹{selectedType.consultation_fee} due at check-in</strong>
                    )
                  ) : (
                    <span className="muted">Not selected</span>
                  )}
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

      {showEditModal && selectedPatient && (
        <PatientFormModal
          mode="edit"
          patient={selectedPatient}
          title="Verify / edit patient details"
          onClose={() => setShowEditModal(false)}
          onSaved={handlePatientEdited}
        />
      )}
    </section>
  )
}
