import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight,
  CalendarBlank,
  CaretLeft,
  CaretRight,
  CheckCircle,
  Clock,
  CurrencyInr,
  MagnifyingGlass,
  User,
} from '@phosphor-icons/react'
import {
  ApiError,
  confirmAndCheckInAdmin,
  createAdminAppointment,
  getAppConfig,
  getDoctorDepartments,
  getDoctorsForDateByDepartment,
  listAppointmentTypesForDepartment,
  listAppointmentTypesForDoctor,
  listDepartments,
  searchPatientsAdmin,
  settleFreeVisitAdmin,
} from '../api'
import type {
  AppointmentType,
  AppointmentTypeSummary,
  ArrivalActionResult,
  BookingSource,
  Department,
  DoctorWithSlots,
  PaymentActionResult,
  Patient,
  Slot,
} from '../types'
import { formatDate, formatPreciseAge, formatTime } from '../format'
import { isoDateToday } from './doctorSchedule'
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

// Minutes since local midnight, read straight out of the ISO string's own
// HH:MM digits -- never through `new Date(...)`, which would silently
// reinterpret a "2026-10-06T09:30:00+05:30" through the *viewer's* zone
// instead of the doctor-local wall-clock time it actually is. Same
// discipline format.ts's own formatTime already documents and follows;
// the grid's positioning math needs the identical guarantee, not just
// the labels.
function minutesOfDay(isoString: string): number {
  const match = isoString.match(/T(\d{2}):(\d{2}):/)
  if (!match) return 0
  return Number(match[1]) * 60 + Number(match[2])
}

function formatHourTick(totalMinutes: number): string {
  const hour24 = Math.floor(totalMinutes / 60) % 24
  const suffix = hour24 >= 12 ? 'PM' : 'AM'
  let hour = hour24 % 12
  if (hour === 0) hour = 12
  return `${hour} ${suffix}`
}

function slotKey(doctorId: number, slot: Slot): string {
  return `${doctorId}:${slot.start_at}`
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
//
// Single-screen scheduler (Book Appointment redesign, approved after a
// mockup discussion -- see docs/ux/SCREEN_MAP.md's entry for this
// component): a persistent patient panel, a department x appointment-
// type x date x doctor grid replacing the old doctor-first step
// wizard, and a persistent summary panel, instead of a linear 1-2-3-4
// wizard where every step was pre-expanded regardless of whether it
// could be reached yet. Order is Department -> Appointment Type ->
// Date -> Doctors-with-slots, matching the already-built, already-
// tested Date-First flow (app/api/scheduling.py's WhatsApp flow, app/
// api/patient_scheduling.py's web flow) exactly, not a new ordering --
// appointment_types are listed per-department (GET /departments/{id}/
// appointment-types), never globally, and GET /appointments/
// availability/by-department (this redesign's one new backend
// endpoint, a thin staff-auth'd wrapper around the same list_doctors_
// with_slots_for_date the patient-facing endpoint calls, exempt from
// the patient scheduling window the same way GET /appointments/
// calendar already is) requires both a department and an appointment
// type up front.
//
// Duration is per (doctor, appointment type) -- doctor_appointment_
// types.duration_minutes, only meaningful once a specific doctor is
// known -- so DoctorWithSlots (the grid's own data) never carries it;
// each doctor's row width comes from that doctor's own real slots'
// start_at/end_at, and the exact fee/duration for the review panel and
// the success screen's free-visit check is fetched via the existing
// listAppointmentTypesForDoctor once a specific doctor is picked off
// the grid (the same call the old doctor-first wizard made when a
// doctor was chosen from its dropdown -- only the trigger moved).
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
  // PatientFormModal directly -- registration is never the starting
  // point of an OPD visit, the receptionist always searches first.
  // This still opens the register modal for them (skipping the extra
  // click), but only after landing on the search step, matching every
  // other patient-registration entry point.
  autoOpenRegister?: boolean
}) {
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

  const [departments, setDepartments] = useState<Department[]>([])
  const [departmentsLoading, setDepartmentsLoading] = useState(true)
  const [departmentId, setDepartmentId] = useState('')

  const [deptTypes, setDeptTypes] = useState<AppointmentTypeSummary[]>([])
  const [deptTypesLoading, setDeptTypesLoading] = useState(false)
  const [appointmentTypeId, setAppointmentTypeId] = useState('')

  const [date, setDate] = useState(isoDateToday())

  const [doctorsWithSlots, setDoctorsWithSlots] = useState<DoctorWithSlots[]>([])
  const [gridLoading, setGridLoading] = useState(false)
  const [gridError, setGridError] = useState<string | null>(null)

  const [selectedDoctorId, setSelectedDoctorId] = useState<number | null>(null)
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)
  // Roving tabindex (WAI-ARIA grid pattern): exactly one slot button is
  // ever a real Tab stop. Without this, a 5-doctor x dozens-of-slots
  // grid would put a full row of tiny buttons between every other
  // control on the page and the next -- the wizard this replaces never
  // had that problem (a handful of controls per step), so it never
  // needed this, but a grid this dense does.
  const [activeCellKey, setActiveCellKey] = useState<string | null>(null)
  const cellRefs = useRef(new Map<string, HTMLButtonElement>())

  // The specific doctor's own fee/duration for the chosen appointment
  // type -- fetched only once a doctor is actually picked off the grid
  // (DoctorWithSlots itself carries neither, since both are per-doctor,
  // not global). The exact same listAppointmentTypesForDoctor call the
  // old doctor-first wizard made on doctor selection; only when it
  // fires moved.
  const [selectedDoctorTypes, setSelectedDoctorTypes] = useState<AppointmentType[]>([])
  const [selectedDoctorTypesLoading, setSelectedDoctorTypesLoading] = useState(false)

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
    // Snapshot of the chosen fee at booking time -- resetForm() clears
    // the selection right after this is set, so the success screen
    // (and the auto-settle-free-visit call below) need their own copy.
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
  // Payment must be configurable, not hardcoded mandatory: a walk-in
  // whose selected appointment type has no consultation fee configured
  // settles automatically the moment check-in succeeds (settle_free_
  // visit_service), instead of forcing staff through a manual Collect
  // Payment/Waive Charge step for a genuinely free visit. A nonzero fee
  // is entirely unaffected -- that patient's payment step still happens
  // on the Appointments page, unchanged.
  const [paymentSettleResult, setPaymentSettleResult] = useState<PaymentActionResult | null>(null)
  const [paymentSettleError, setPaymentSettleError] = useState<string | null>(null)
  // The success screen's "Department" line -- fetched once justBooked
  // is set, same "earliest-assigned = primary" convention DoctorWorkspace.
  // tsx already uses, since an appointment itself carries no department
  // (a doctor can offer the same appointment type across more than one).
  const [justBookedDepartment, setJustBookedDepartment] = useState<string | null>(null)

  useEffect(() => {
    listDepartments()
      .then((ds) => {
        setDepartments(ds)
        // Default to the first department rather than leaving the grid
        // unreachable until staff makes an explicit pick -- "no dead-end
        // pages" (docs/ux/UX_PRINCIPLES.md); still freely changeable via
        // the department pills below.
        if (ds.length > 0) setDepartmentId(String(ds[0].id))
      })
      .catch(() => undefined)
      .finally(() => setDepartmentsLoading(false))
    getAppConfig()
      .then((c) => setTimezoneLabel(c.default_timezone))
      .catch(() => undefined)
  }, [])

  // Backend-driven search (GET /patients/search), not a client-side
  // filter over the whole registry -- the receptionist must be able to
  // positively identify (or rule out) an existing patient by name,
  // mobile number, or UHID before ever reaching the registration form,
  // and that has to scale past however many patients this clinic has on
  // file. Debounced (300ms) so every keystroke doesn't fire its own
  // request; a stale response for a since-changed query is dropped via
  // the `cancelled` guard, same pattern the grid fetch below uses.
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

  // Appointment types are listed per-department (no doctor known yet),
  // matching the Date-First flow's own step order exactly -- see this
  // component's module docstring for why that's the order and not the
  // reverse.
  useEffect(() => {
    setAppointmentTypeId('')
    setSelectedDoctorId(null)
    setSelectedSlot(null)
    if (!departmentId) {
      setDeptTypes([])
      return
    }
    let cancelled = false
    setDeptTypesLoading(true)
    listAppointmentTypesForDepartment(Number(departmentId))
      .then((ts) => {
        if (!cancelled) setDeptTypes(ts)
      })
      .catch(() => {
        if (!cancelled) setDeptTypes([])
      })
      .finally(() => {
        if (!cancelled) setDeptTypesLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [departmentId])

  useEffect(() => {
    setSelectedDoctorId(null)
    setSelectedSlot(null)
    setSelectedDoctorTypes([])
    if (!departmentId || !appointmentTypeId || !date) {
      setDoctorsWithSlots([])
      return
    }
    let cancelled = false
    setGridLoading(true)
    setGridError(null)
    getDoctorsForDateByDepartment(Number(departmentId), Number(appointmentTypeId), date)
      .then((result) => {
        if (cancelled) return
        setDoctorsWithSlots(result.doctors)
      })
      .catch((err) => {
        if (cancelled) return
        setGridError(err instanceof ApiError ? err.message : 'Unable to load availability. Please try again.')
        setDoctorsWithSlots([])
      })
      .finally(() => {
        if (!cancelled) setGridLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [departmentId, appointmentTypeId, date])

  useEffect(() => {
    if (!selectedDoctorId) {
      setSelectedDoctorTypes([])
      return
    }
    let cancelled = false
    setSelectedDoctorTypesLoading(true)
    listAppointmentTypesForDoctor(selectedDoctorId)
      .then((ts) => {
        if (!cancelled) setSelectedDoctorTypes(ts)
      })
      .catch(() => {
        if (!cancelled) setSelectedDoctorTypes([])
      })
      .finally(() => {
        if (!cancelled) setSelectedDoctorTypesLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedDoctorId])

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
  // progress (department/type/doctor/slot already chosen) is untouched.
  function handlePatientEdited(p: Patient) {
    setSelectedPatient(p)
  }

  const isWalkIn = bookingSource === 'WALK_IN'
  const selectedDoctor = doctorsWithSlots.find((d) => d.id === selectedDoctorId) ?? null
  const selectedDoctorType = selectedDoctorTypes.find((t) => String(t.id) === appointmentTypeId) ?? null

  // What's still missing before this can be submitted, in the order the
  // page's own panels are laid out -- null once everything required is
  // in place. Drives both the Book button's disabled state and the
  // helper text in the summary panel, so the two can never drift out of
  // sync.
  function missingSelectionMessage(): string | null {
    if (!selectedPatient) return 'Search for or register a patient to continue.'
    if (!departmentId) return 'Select a department to continue.'
    if (!appointmentTypeId) return 'Select an appointment type to continue.'
    if (!selectedDoctor || !selectedSlot) return 'Pick an available doctor and time slot to continue.'
    if (!selectedDoctorType) {
      return selectedDoctorTypesLoading ? 'Loading this appointment’s details…' : 'This doctor does not offer this appointment type.'
    }
    return null
  }

  const missingSelection = missingSelectionMessage()
  const canSubmit = !busy && missingSelection === null

  function resetForm() {
    setSelectedPatient(null)
    setPatientSearch('')
    setAppointmentTypeId('')
    setSelectedDoctorId(null)
    setSelectedSlot(null)
    // Department/date deliberately left as-is -- staff booking several
    // visits in a row are almost always still in the same department on
    // the same day.
  }

  async function handleSubmit() {
    // The Book button is disabled whenever missingSelectionMessage() is
    // non-null (same fields checked there), so this should be
    // unreachable in normal use -- kept as a guard, not silently, so a
    // click that somehow gets through with an incomplete selection
    // surfaces an actual error instead of doing nothing.
    if (!selectedPatient || !selectedDoctor || !selectedDoctorType || !selectedSlot) {
      setError('Some required fields are missing. Please review your selection and try again.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      const created = await createAdminAppointment(
        selectedDoctor.id,
        selectedPatient.id,
        selectedDoctorType.id,
        selectedSlot.start_at,
        bookingSource,
      )
      setJustBooked({
        doctorId: selectedDoctor.id,
        patientId: selectedPatient.id,
        patientUhid: selectedPatient.uhid,
        appointmentTypeName: selectedDoctorType.name,
        consultationFee: selectedDoctorType.consultation_fee,
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
  // right away -- the receptionist never sees a Collect Payment/Waive
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
  }

  function stepDay(delta: number) {
    const next = addDays(date || today, delta)
    if (next < today) return
    setDate(next)
  }

  // Grid axis bounds, in minutes-since-midnight, spanning every real
  // returned slot across every doctor -- never a hardcoded "9am-5pm"
  // assumption, so a department whose doctors run earlier/later/longer
  // hours still lays out correctly. Padded out to the nearest whole
  // hour on each side purely so the header's hour ticks land on clean
  // boundaries.
  const gridBounds = useMemo(() => {
    let min = Infinity
    let max = -Infinity
    for (const doc of doctorsWithSlots) {
      for (const s of doc.slots) {
        const startMin = minutesOfDay(s.start_at)
        const endMin = minutesOfDay(s.end_at)
        if (startMin < min) min = startMin
        if (endMin > max) max = endMin
      }
    }
    if (!isFinite(min) || !isFinite(max) || min >= max) return null
    return { start: Math.floor(min / 60) * 60, end: Math.ceil(max / 60) * 60 }
  }, [doctorsWithSlots])

  const hourTicks = useMemo(() => {
    if (!gridBounds) return []
    const ticks: number[] = []
    for (let m = gridBounds.start; m <= gridBounds.end; m += 60) ticks.push(m)
    return ticks
  }, [gridBounds])

  function pct(totalMinutes: number): number {
    if (!gridBounds) return 0
    return ((totalMinutes - gridBounds.start) / (gridBounds.end - gridBounds.start)) * 100
  }

  // Roving tabindex's "current" cell -- state when the grid has been
  // navigated, otherwise the first doctor's first slot, recomputed
  // (never stored/synced via an effect) so a data reload never steals
  // focus onto the grid on its own; only an explicit arrow-key press
  // (handleGridKeyDown below) ever calls .focus() directly.
  const firstCellKey =
    doctorsWithSlots.length > 0 && doctorsWithSlots[0].slots.length > 0
      ? slotKey(doctorsWithSlots[0].id, doctorsWithSlots[0].slots[0])
      : null
  const activeKeyIsValid =
    activeCellKey !== null && doctorsWithSlots.some((d) => d.slots.some((s) => slotKey(d.id, s) === activeCellKey))
  const effectiveActiveKey = activeKeyIsValid ? activeCellKey : firstCellKey

  function pickSlot(doctorId: number, slot: Slot) {
    setSelectedDoctorId(doctorId)
    setSelectedSlot(slot)
    setActiveCellKey(slotKey(doctorId, slot))
  }

  // WAI-ARIA grid pattern: Left/Right move within a doctor's own row
  // (their slot order), Up/Down move to the nearest-start-time slot in
  // the adjacent doctor's row (row lengths and start times generally
  // differ -- there's no "same column" to preserve), Enter/Space
  // selects. Focus is moved synchronously, inside the key handler
  // itself, never via a reactive effect (see effectiveActiveKey above
  // for why).
  function handleGridKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, doctorIndex: number, slotIndex: number) {
    const rows = doctorsWithSlots
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      const doc = rows[doctorIndex]
      pickSlot(doc.id, doc.slots[slotIndex])
      return
    }

    let targetDoctorIndex = doctorIndex
    let targetSlotIndex = slotIndex

    if (e.key === 'ArrowRight') {
      targetSlotIndex = slotIndex + 1
      if (targetSlotIndex >= rows[doctorIndex].slots.length) return
    } else if (e.key === 'ArrowLeft') {
      targetSlotIndex = slotIndex - 1
      if (targetSlotIndex < 0) return
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      targetDoctorIndex = doctorIndex + (e.key === 'ArrowDown' ? 1 : -1)
      if (targetDoctorIndex < 0 || targetDoctorIndex >= rows.length) return
      const targetRow = rows[targetDoctorIndex]
      if (targetRow.slots.length === 0) return
      const currentStart = minutesOfDay(rows[doctorIndex].slots[slotIndex].start_at)
      let bestIndex = 0
      let bestDiff = Infinity
      targetRow.slots.forEach((s, i) => {
        const diff = Math.abs(minutesOfDay(s.start_at) - currentStart)
        if (diff < bestDiff) {
          bestDiff = diff
          bestIndex = i
        }
      })
      targetSlotIndex = bestIndex
    } else {
      return
    }

    e.preventDefault()
    const targetDoc = rows[targetDoctorIndex]
    const targetSlot = targetDoc.slots[targetSlotIndex]
    const key = slotKey(targetDoc.id, targetSlot)
    setActiveCellKey(key)
    cellRefs.current.get(key)?.focus()
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

          {/* "Print Token" calls window.print() scoped to this
              print-only slip (print-area + print-only, styles.css) so
              it never prints the whole visible page. */}
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

  const summaryPatientLabel = selectedPatient ? selectedPatient.name : 'Search or select a patient'
  const summaryTypeLabel = (() => {
    const t = deptTypes.find((dt) => String(dt.id) === appointmentTypeId)
    if (!t) return 'Not selected'
    return selectedDoctorType ? `${t.name} · ₹${selectedDoctorType.consultation_fee}` : t.name
  })()
  const summarySlotLabel =
    selectedDoctor && selectedSlot ? `${selectedDoctor.name} · ${formatTime(selectedSlot.start_at)}` : 'Pick an open slot on the grid'
  const bookLabel = busy
    ? 'Booking…'
    : canSubmit
      ? 'Book Appointment'
      : !selectedPatient
        ? 'Select a patient first'
        : !appointmentTypeId
          ? 'Select an appointment type'
          : !selectedSlot
            ? 'Pick a time slot'
            : 'Loading…'

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

      <div className="book-scheduler-layout">
        {/* Persistent patient panel -- not a gated step, always visible
            and switchable regardless of what else has been picked. */}
        <div className="book-scheduler-patient">
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
                      <CheckCircle size={14} weight="bold" aria-hidden="true" /> Selected
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

          <div className="book-step-card">
            <div className="book-step-heading">
              <span className={`book-step-number${appointmentTypeId ? ' done' : departmentId ? ' current' : ''}`} aria-hidden="true">
                2
              </span>
              <div>
                <h3>Appointment type</h3>
                <p className="muted">{deptTypesLoading ? 'Loading…' : 'Duration/fee depend on the doctor you pick.'}</p>
              </div>
            </div>
            <div role="radiogroup" aria-label="Appointment type" className="book-type-list">
              {deptTypes.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  role="radio"
                  aria-checked={String(t.id) === appointmentTypeId}
                  className={String(t.id) === appointmentTypeId ? 'book-type-option active' : 'book-type-option'}
                  disabled={!departmentId}
                  onClick={() => setAppointmentTypeId(String(t.id))}
                >
                  {t.name}
                </button>
              ))}
              {!deptTypesLoading && departmentId && deptTypes.length === 0 && (
                <p className="muted">No appointment types are configured for this department yet.</p>
              )}
            </div>
          </div>
        </div>

        {/* Grid: department filter + date nav above a doctor x time
            grid, gated on an appointment type being chosen (GET
            /appointments/availability/by-department requires one). */}
        <div className="book-scheduler-main">
          <div className="book-scheduler-filters">
            <div className="book-scheduler-filter-group">
              <span className="book-scheduler-filter-label">Department</span>
              {departmentsLoading ? (
                <span className="muted">Loading…</span>
              ) : (
                departments.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    className={String(d.id) === departmentId ? 'date-scope-pill active' : 'date-scope-pill'}
                    onClick={() => setDepartmentId(String(d.id))}
                  >
                    {d.name}
                  </button>
                ))
              )}
            </div>
            <div className="book-date-nav" style={{ marginBottom: 0 }}>
              <button type="button" className="icon-btn" aria-label="Previous day" onClick={() => stepDay(-1)}>
                <CaretLeft size={16} />
              </button>
              <button
                type="button"
                className={date === today ? 'date-scope-pill active' : 'date-scope-pill'}
                onClick={() => jumpToDate(today)}
              >
                Today
              </button>
              <button
                type="button"
                className={date === addDays(today, 1) ? 'date-scope-pill active' : 'date-scope-pill'}
                onClick={() => jumpToDate(addDays(today, 1))}
              >
                Tomorrow
              </button>
              <label className="book-date-nav-input">
                <CalendarBlank size={15} weight="bold" aria-hidden="true" />
                <input
                  type="date"
                  aria-label="Jump to date"
                  min={today}
                  value={date}
                  onChange={(e) => e.target.value && jumpToDate(e.target.value)}
                />
              </label>
              <button type="button" className="icon-btn" aria-label="Next day" onClick={() => stepDay(1)}>
                <CaretRight size={16} />
              </button>
            </div>
          </div>

          {appointmentTypeId ? (
            <div className="book-grid-card">
              {gridError && <p className="error" style={{ margin: 'var(--space-3) var(--space-4) 0' }}>{gridError}</p>}
              {gridLoading && (
                <div className="state-block">
                  <span className="spinner" aria-hidden="true" />
                  Loading availability…
                </div>
              )}
              {!gridLoading && !gridError && doctorsWithSlots.length === 0 && (
                <div className="state-block empty">No doctors offer this appointment type in this department.</div>
              )}
              {!gridLoading && !gridError && doctorsWithSlots.length > 0 && gridBounds && (
                <>
                  <div className="book-grid-header">
                    <div className="book-grid-header-doctor-col">Doctor</div>
                    <div className="book-grid-header-hours">
                      {hourTicks.map((m) => (
                        <div key={m} className="book-grid-hour-label" style={{ left: `${pct(m)}%` }}>
                          {formatHourTick(m)}
                        </div>
                      ))}
                    </div>
                  </div>
                  {doctorsWithSlots.map((doc, doctorIndex) => {
                    const duration = doc.slots.length > 0 ? minutesOfDay(doc.slots[0].end_at) - minutesOfDay(doc.slots[0].start_at) : null
                    return (
                      <div className="book-grid-row" key={doc.id}>
                        <div className="book-grid-row-doctor">
                          <strong>{doc.name}</strong>
                          <span className="muted">
                            {doc.specialization}
                            {duration != null ? ` · ${duration}-min slots` : ''}
                          </span>
                        </div>
                        <div className="book-grid-row-track">
                          {doc.slots.length === 0 ? (
                            <span className="muted" style={{ fontSize: '0.72rem', position: 'absolute', top: 16, left: 12 }}>
                              Unavailable
                            </span>
                          ) : (
                            doc.slots.map((slot, slotIndex) => {
                              const key = slotKey(doc.id, slot)
                              const isSelected = selectedDoctorId === doc.id && selectedSlot?.start_at === slot.start_at
                              const left = pct(minutesOfDay(slot.start_at))
                              const width = pct(minutesOfDay(slot.end_at)) - left
                              return (
                                <button
                                  key={key}
                                  ref={(el) => {
                                    if (el) cellRefs.current.set(key, el)
                                    else cellRefs.current.delete(key)
                                  }}
                                  type="button"
                                  className={isSelected ? 'book-grid-slot selected' : 'book-grid-slot'}
                                  style={{ left: `calc(${left}% + 2px)`, width: `calc(${width}% - 4px)` }}
                                  tabIndex={key === effectiveActiveKey ? 0 : -1}
                                  title={`${doc.name} · ${formatTime(slot.start_at)}`}
                                  aria-label={`${doc.name}, ${formatTime(slot.start_at)}`}
                                  aria-pressed={isSelected}
                                  onClick={() => pickSlot(doc.id, slot)}
                                  onKeyDown={(e) => handleGridKeyDown(e, doctorIndex, slotIndex)}
                                />
                              )
                            })
                          )}
                        </div>
                      </div>
                    )
                  })}
                </>
              )}
            </div>
          ) : (
            <div className="book-grid-placeholder">
              Pick an appointment type on the left to see who's available.
            </div>
          )}

          <div className="book-grid-legend">
            <div className="book-grid-legend-items">
              <div className="book-grid-legend-item">
                <span className="book-grid-legend-swatch available" /> Available
              </div>
              <div className="book-grid-legend-item">
                <span className="book-grid-legend-swatch selected" /> Selected
              </div>
              <div className="book-grid-legend-item">
                <span className="book-grid-legend-swatch unavailable" /> Unavailable
              </div>
            </div>
            <span className="book-grid-legend-hint">Tab into the grid once, then arrow keys between slots, Enter to pick</span>
          </div>
        </div>

        {/* Persistent summary -- fills in progressively, not six
            "Not selected" rows shown from the very start. */}
        <div className="book-appointment-side">
          <div className="book-review-card">
            <h3>This appointment</h3>

            <div className="book-review-list" style={{ gap: 'var(--space-2)' }}>
              <div className={selectedPatient ? 'book-summary-item filled' : 'book-summary-item'} style={{ border: 'none' }}>
                <span className="book-summary-item-icon">
                  <User size={14} weight="bold" aria-hidden="true" />
                </span>
                <div className="book-summary-item-body">
                  <div className="book-summary-item-label">Patient</div>
                  <div className="book-summary-item-value">{summaryPatientLabel}</div>
                </div>
              </div>
              <div className={appointmentTypeId ? 'book-summary-item filled' : 'book-summary-item'} style={{ border: 'none' }}>
                <span className="book-summary-item-icon">
                  <CurrencyInr size={14} weight="bold" aria-hidden="true" />
                </span>
                <div className="book-summary-item-body">
                  <div className="book-summary-item-label">Type</div>
                  <div className="book-summary-item-value">{summaryTypeLabel}</div>
                </div>
              </div>
              <div className={selectedSlot ? 'book-summary-item filled' : 'book-summary-item'} style={{ border: 'none' }}>
                <span className="book-summary-item-icon">
                  <Clock size={14} weight="bold" aria-hidden="true" />
                </span>
                <div className="book-summary-item-body">
                  <div className="book-summary-item-label">Doctor &amp; time</div>
                  <div className="book-summary-item-value">{summarySlotLabel}</div>
                </div>
              </div>
            </div>

            {timezoneLabel && <p className="muted" style={{ fontSize: '0.72rem' }}>Doctor's time zone: {timezoneLabel}</p>}

            {!busy && (
              <p className="muted book-review-hint" aria-live="polite">
                {missingSelection ?? 'Ready to book -- review the details above, then confirm.'}
              </p>
            )}

            <button type="button" className="btn book-review-cta" disabled={!canSubmit} onClick={handleSubmit}>
              <CalendarBlank size={16} weight="bold" aria-hidden="true" /> {bookLabel}
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
