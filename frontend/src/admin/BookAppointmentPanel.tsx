import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight,
  CalendarBlank,
  CaretLeft,
  CaretRight,
  CheckCircle,
  Lock,
  MagnifyingGlass,
} from '@phosphor-icons/react'
import {
  ApiError,
  confirmAndCheckInAdmin,
  createAdminAppointment,
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
import { formatAgeCompact, formatDate, formatDateWithWeekday, formatPreciseAge, formatTime } from '../format'
import { clinicClockLabel, isoDateToday } from './doctorSchedule'
import PatientFormModal from './PatientFormModal'

// Now persisted on the appointment (migrations/0023) -- see types.ts's
// BookingSource for the ONLINE/PHONE/WALK_IN/STAFF_ASSISTED reasoning.
// Walk-in leads because it is the overwhelmingly common front-desk
// case and the one this screen defaults to. `hint` is the access key
// the keyboard map below binds (W/P/O/S) and the pill prints.
const BOOKING_SOURCES: { key: BookingSource; label: string; hint: string }[] = [
  { key: 'WALK_IN', label: 'Walk-in', hint: 'W' },
  { key: 'PHONE', label: 'Phone', hint: 'P' },
  { key: 'ONLINE', label: 'Online', hint: 'O' },
  { key: 'STAFF_ASSISTED', label: 'Staff-assisted', hint: 'S' },
]

const SOURCE_KEY_TO_SOURCE: Record<string, BookingSource> = {
  w: 'WALK_IN',
  p: 'PHONE',
  o: 'ONLINE',
  s: 'STAFF_ASSISTED',
}

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


// A slot chip's floor. WCAG 2.5.8 puts the minimum pointer target at
// 44px, but a chip also has to hold its own label, and the widest one
// ("10:15 PM") needs more than that at this type scale -- at 44px the
// late-evening slots of a short appointment type clipped. So the floor
// is the label's requirement, which clears the accessibility minimum
// comfortably rather than sitting exactly on it.
const SLOT_MIN_WIDTH_PX = 68

// Each doctor's slot columns are sized from *their own* slot length,
// not from a fixed hour. The grid this replaces laid every slot out as
// a percentage of a padded whole-hour axis, which made a 35-minute
// slot 26px of unlabelled sliver on a real laptop viewport -- the
// shorter the appointment type, the less clickable and less readable
// its own screen became. Sizing by duration instead means a longer
// appointment reads as a wider chip (which is true and useful), while
// the floor above keeps a short one usable.
function slotColumnWidth(durationMinutes: number | null): number {
  if (durationMinutes === null || durationMinutes <= 0) return SLOT_MIN_WIDTH_PX
  return Math.max(SLOT_MIN_WIDTH_PX, Math.round(durationMinutes * 2))
}

function slotKey(doctorId: number, slot: Slot): string {
  return `${doctorId}:${slot.start_at}`
}

// Whole minutes from `nowMs` until an ISO instant. Unlike every other
// time helper here this one DOES go through `new Date(...)`, and must:
// the ISO string carries its own offset, so parsing it yields the
// correct absolute instant whatever the viewer's device zone is, and an
// absolute instant is exactly what "how long until this slot" needs.
// Reading the wall-clock digits (minutesOfDay above) would instead
// compare a clinic-local time against a device-local clock, which is
// only right when the two zones agree.
function minutesUntil(isoString: string, nowMs: number): number {
  const target = new Date(isoString).getTime()
  if (Number.isNaN(target)) return 0
  return Math.round((target - nowMs) / 60000)
}

// "in 8 min" / "in 2 hr 10 min" / "now" -- the distance a receptionist
// reads off the Next available card. Only ever shown for a slot on the
// current clinic date (see etaFor below): on a future date the number
// of minutes is both enormous and useless, and the date says it better.
function formatEta(minutes: number): string {
  if (minutes <= 0) return 'now'
  if (minutes < 60) return `in ${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest === 0 ? `in ${hours} hr` : `in ${hours} hr ${rest} min`
}

// "Today, Wed 1 Oct 2026" / "Tomorrow, ..." / "Fri, 3 Oct 2026" -- the
// date as a statement rather than a control, which is what walk-in
// needs (the date is locked) and what the other sources' nav sits
// beside.
function dayContextLabel(dateStr: string, todayStr: string): string {
  const full = formatDateWithWeekday(dateStr)
  // "Today, Thu 1 Oct 2026" -- the weekday's own comma is dropped when
  // Today/Tomorrow already supplies one, so the line never reads
  // "Today, Thu, 1 Oct 2026".
  const unpunctuated = full.replace(', ', ' ')
  if (dateStr === todayStr) return `Today, ${unpunctuated}`
  if (dateStr === addDays(todayStr, 1)) return `Tomorrow, ${unpunctuated}`
  return full
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
  // Ticks so "in 8 min" and the header's "now" stay honest while the
  // screen sits open at a front desk. 15s matches LiveClock's own
  // cadence -- nothing here shows seconds, but a full minute of a
  // visibly stale countdown reads as broken.
  const [nowMs, setNowMs] = useState(() => Date.now())

  const [bookingSource, setBookingSource] = useState<BookingSource>('WALK_IN')

  const [patientSearch, setPatientSearch] = useState('')
  const [patientResults, setPatientResults] = useState<Patient[]>([])
  // Which search result the arrow keys are on, so a patient can be
  // picked without a mouse. -1 = none highlighted yet.
  const [highlightIndex, setHighlightIndex] = useState(-1)
  const patientSearchRef = useRef<HTMLInputElement | null>(null)
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

  // Duration and fee are properties of a (doctor, appointment type)
  // assignment -- the department-level type list (AppointmentTypeSummary)
  // carries neither, by design. So the visit-type buttons can only show
  // real numbers once SOME doctor is known to read them from, and they
  // say whose they are rather than implying a single clinic-wide value.
  // The soonest-available doctor is that reference, because they are
  // also the one the Next available card is offering.
  const [referenceDoctorTypes, setReferenceDoctorTypes] = useState<AppointmentType[]>([])

  // Walk-in only. Both default on: the patient is physically at the
  // desk, so the overwhelmingly common case is book-and-check-in, and
  // a token that gets generated is wanted on paper.
  const [checkInNow, setCheckInNow] = useState(true)
  const [printTokenSlip, setPrintTokenSlip] = useState(true)

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
  }, [])

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 15000)
    return () => clearInterval(id)
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
          setHighlightIndex(-1)
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

  // Walk-in is, by definition, a patient standing at the desk right
  // now: there is no future date to book them into, so the date stops
  // being a control at all and the picker disappears with it. Switching
  // away from walk-in hands the date back unchanged.
  useEffect(() => {
    if (bookingSource === 'WALK_IN') setDate(isoDateToday())
  }, [bookingSource])

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

  // `override` lets the Next available card (and Enter pressed with
  // nothing picked) book a doctor+slot that was never put into
  // selection state -- the whole point of that card is one keystroke,
  // not select-then-confirm. Everything else about the booking,
  // including which appointment-type assignment supplies the fee, is
  // resolved the same way for both paths.
  async function handleSubmit(override?: { doctor: DoctorWithSlots; slot: Slot }) {
    const doctor = override?.doctor ?? selectedDoctor
    const slot = override?.slot ?? selectedSlot
    // Duration and fee are per (doctor, type). For the selected doctor
    // they are already loaded; for an override they may not be, so they
    // are fetched for that doctor before booking rather than borrowed
    // from whoever happened to be selected.
    if (!selectedPatient || !doctor || !slot || !appointmentTypeId) {
      setError('Some required fields are missing. Please review your selection and try again.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      const doctorTypes =
        doctor.id === selectedDoctorId && selectedDoctorTypes.length > 0
          ? selectedDoctorTypes
          : await listAppointmentTypesForDoctor(doctor.id)
      const doctorType = doctorTypes.find((t) => String(t.id) === appointmentTypeId)
      if (!doctorType) {
        setError('This doctor does not offer this appointment type.')
        return
      }

      const created = await createAdminAppointment(
        doctor.id,
        selectedPatient.id,
        doctorType.id,
        slot.start_at,
        bookingSource,
      )
      setJustBooked({
        doctorId: doctor.id,
        patientId: selectedPatient.id,
        patientUhid: selectedPatient.uhid,
        appointmentTypeName: doctorType.name,
        consultationFee: doctorType.consultation_fee,
        appointmentId: created.id,
        doctorName: doctor.name,
        patientName: selectedPatient.name,
        slot,
        bookingSource,
      })
      setArrivalResult(null)
      setArrivalError(null)
      setPaymentSettleResult(null)
      setPaymentSettleError(null)
      resetForm()

      // The walk-in patient is already standing there, so "Check in
      // now" (on by default) does at booking time exactly what the
      // success screen's own button does -- the receptionist should not
      // have to press a second button to state something that was
      // already true when they started.
      if (bookingSource === 'WALK_IN' && checkInNow) {
        await runCheckIn(created.id, doctorType.consultation_fee)
      }
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
  // Takes its appointment id and fee as arguments rather than reading
  // justBooked, so handleSubmit can run it in the same tick it books --
  // the state setter above has not committed yet at that point.
  async function runCheckIn(appointmentId: number, consultationFee: number) {
    setArrivalBusy(true)
    setArrivalError(null)
    try {
      const result = await confirmAndCheckInAdmin(appointmentId)
      setArrivalResult(result)
      if (result.arrival_kind === 'checked_in' && consultationFee === 0) {
        try {
          const settled = await settleFreeVisitAdmin(appointmentId)
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

  function handleConfirmAndCheckIn() {
    if (!justBooked) return
    void runCheckIn(justBooked.appointmentId, justBooked.consultationFee)
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


  // The three lists the centre column is built from. A doctor with
  // slots is bookable and sorts by their own first free time, because
  // "who can see this patient soonest" is the only question being asked
  // at a walk-in desk. A doctor without slots cannot be ranked that way
  // and collapses instead -- and the two reasons they have none are not
  // the same fact: total_slots is that day's capacity, so >0 with
  // nothing left means genuinely fully booked, while 0 means they hold
  // no clinic that day at all. The endpoint returns both (include_
  // unavailable=True); conflating them would tell staff a doctor is
  // booked solid when they are simply off.
  const bookableDoctors = useMemo(
    () =>
      doctorsWithSlots
        .filter((d) => d.slots.length > 0)
        .sort((a, b) => minutesOfDay(a.slots[0].start_at) - minutesOfDay(b.slots[0].start_at)),
    [doctorsWithSlots],
  )

  const unbookableDoctors = useMemo(
    () => doctorsWithSlots.filter((d) => d.slots.length === 0),
    [doctorsWithSlots],
  )

  const nextAvailable = useMemo(() => {
    const doc = bookableDoctors[0]
    if (!doc) return null
    return { doctor: doc, slot: doc.slots[0] }
  }, [bookableDoctors])

  const runnerUp = useMemo(() => {
    const doc = bookableDoctors[1]
    if (!doc) return null
    return { doctor: doc, slot: doc.slots[0] }
  }, [bookableDoctors])

  const isToday = date === today

  // Only ever shown for a slot on the current clinic date -- see
  // formatEta. On a future date the minute count is both huge and
  // meaningless, so the date label carries it instead.
  function etaFor(slot: Slot): string | null {
    if (!isToday) return null
    return formatEta(minutesUntil(slot.start_at, nowMs))
  }

  // Roving tabindex's "current" cell -- state when the grid has been
  // navigated, otherwise the first doctor's first slot, recomputed
  // (never stored/synced via an effect) so a data reload never steals
  // focus onto the grid on its own; only an explicit arrow-key press
  // (handleGridKeyDown below) ever calls .focus() directly.
  const firstCellKey =
    bookableDoctors.length > 0 ? slotKey(bookableDoctors[0].id, bookableDoctors[0].slots[0]) : null
  const activeKeyIsValid =
    activeCellKey !== null && bookableDoctors.some((d) => d.slots.some((s) => slotKey(d.id, s) === activeCellKey))
  const effectiveActiveKey = activeKeyIsValid ? activeCellKey : firstCellKey

  // The soonest-available doctor supplies the duration/fee shown on the
  // visit-type buttons. One request, not one per doctor: it returns
  // every type that doctor offers, which is exactly the list being
  // rendered.
  useEffect(() => {
    const referenceId = bookableDoctors[0]?.id
    if (!referenceId) {
      setReferenceDoctorTypes([])
      return
    }
    let cancelled = false
    listAppointmentTypesForDoctor(referenceId)
      .then((ts) => {
        if (!cancelled) setReferenceDoctorTypes(ts)
      })
      .catch(() => {
        if (!cancelled) setReferenceDoctorTypes([])
      })
    return () => {
      cancelled = true
    }
  }, [bookableDoctors])

  // Everything the keyboard map needs that isn't already a plain
  // handler. Deliberately on document, not the section: "/" and the
  // source keys have to work before anything inside this screen has
  // been focused, which is the state a receptionist starts in.
  //
  // Every binding is suppressed while focus is in a text field --
  // otherwise typing a patient called "Walk" would silently change the
  // booking source four times. Modified presses are left alone so
  // browser and OS shortcuts keep working.
  const bookNextAvailable = useCallback(() => {
    if (!nextAvailable) return
    void handleSubmit({ doctor: nextAvailable.doctor, slot: nextAvailable.slot })
    // handleSubmit is redefined every render; depending on it would
    // re-bind the listener constantly for no benefit, and it only ever
    // reads current state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nextAvailable])

  useEffect(() => {
    function isTypingTarget(target: EventTarget | null): boolean {
      if (!(target instanceof HTMLElement)) return false
      const tag = target.tagName
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
    }

    function onKeyDown(e: KeyboardEvent) {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return
      // A modal owns the keyboard while it is open. Without this, Esc
      // pressed to dismiss the register form would also wipe the
      // patient, doctor and slot chosen on the screen behind it, and
      // "/" or a source key typed into that form would leak out here.
      if (document.querySelector('.modal-overlay')) return
      const typing = isTypingTarget(e.target)

      if (e.key === 'Escape') {
        // Always drops focus, not only out of a text field: Esc means
        // "get me back to a clean slate", and leaving focus parked on
        // whatever button was last clicked would swallow the Enter that
        // normally books.
        if (e.target instanceof HTMLElement) e.target.blur()
        setPatientSearch('')
        setHighlightIndex(-1)
        setSelectedDoctorId(null)
        setSelectedSlot(null)
        return
      }

      if (typing) return

      if (e.key === '/') {
        e.preventDefault()
        patientSearchRef.current?.focus()
        return
      }

      const source = SOURCE_KEY_TO_SOURCE[e.key.toLowerCase()]
      if (source) {
        e.preventDefault()
        setBookingSource(source)
        return
      }

      // Enter from anywhere outside the grid books: whatever is
      // selected, or else the Next available offer the card is already
      // showing. A chip handles its own Enter (see handleGridKeyDown),
      // so this never double-fires.
      if (e.key === 'Enter') {
        if (e.target instanceof HTMLElement && e.target.closest('.book-grid-slot')) return
        if (e.target instanceof HTMLElement && e.target.closest('button, a')) return
        if (busy) return
        e.preventDefault()
        if (selectedDoctor && selectedSlot) void handleSubmit()
        else bookNextAvailable()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookNextAvailable, busy, selectedDoctor, selectedSlot])

  // Arrow keys/Enter inside the patient results list, so a patient can
  // be chosen without leaving the search field.
  function handleSearchKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (patientResults.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightIndex((i) => Math.min(i + 1, patientResults.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      const target = highlightIndex >= 0 ? patientResults[highlightIndex] : patientResults[0]
      if (target) {
        e.preventDefault()
        selectPatient(target)
        e.currentTarget.blur()
      }
    }
  }

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
    // Only the bookable rows are navigable -- the collapsed ones hold
    // no chips to move between.
    const rows = bookableDoctors
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      const doc = rows[doctorIndex]
      const slot = doc.slots[slotIndex]
      // First Enter selects; a second Enter on the slot already
      // selected books it, so a keyboard booking never has to leave the
      // grid to find the confirm button.
      const alreadySelected = selectedDoctorId === doc.id && selectedSlot?.start_at === slot.start_at
      if (alreadySelected && e.key === 'Enter') {
        if (!busy) void handleSubmit({ doctor: doc, slot })
        return
      }
      pickSlot(doc.id, slot)
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

  const sourceLabel = BOOKING_SOURCES.find((s) => s.key === bookingSource)?.label ?? 'Walk-in'
  const dateContext = dayContextLabel(date, today)
  const departmentName = departments.find((d) => String(d.id) === departmentId)?.name ?? null
  const summaryTypeName = deptTypes.find((t) => String(t.id) === appointmentTypeId)?.name ?? 'Not selected'

  // The per-(doctor, type) assignment behind the current selection --
  // the only place a real duration and fee exist.
  const summaryDuration = selectedDoctorType ? `${selectedDoctorType.duration_minutes} min` : null
  const summaryFee = selectedDoctorType ? selectedDoctorType.consultation_fee : null

  // The button says what pressing it does, which for a walk-in with
  // "Check in now" ticked is two things, not one.
  const bookLabel = busy
    ? 'Booking…'
    : isWalkIn && checkInNow
      ? 'Book & check in'
      : 'Book appointment'

  return (
    <section className="book-appointment-page">
      <div className="book-page-header">
        <div className="book-page-heading">
          <h2>Book appointment</h2>
          <p className="muted book-page-context">
            {sourceLabel} · {dateContext} · {clinicClockLabel(new Date(nowMs))}
          </p>
        </div>
        <div className="booking-source-picker">
          <span className="field-label">Booking source</span>
          <div className="book-source-pills" role="radiogroup" aria-label="Booking source">
            {BOOKING_SOURCES.map((s) => (
              <button
                key={s.key}
                type="button"
                role="radio"
                aria-checked={s.key === bookingSource}
                className={s.key === bookingSource ? 'book-source-pill active' : 'book-source-pill'}
                onClick={() => setBookingSource(s.key)}
              >
                {s.label}
                <span className="book-key-hint" aria-hidden="true">{s.hint}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="book-scheduler-layout">
        {/* Left: who, and what kind of visit. */}
        <div className="book-scheduler-patient">
          <div className="book-step-card">
            <div className="book-card-head">
              <h3>{isWalkIn ? 'Walk-in patient' : 'Patient'}</h3>
              <span className="book-key-hint" aria-hidden="true">/</span>
            </div>
            <div className="book-step1-body">
              <div className="book-patient-search">
                <label className="filter-bar-search-input book-patient-search-input">
                  <MagnifyingGlass size={16} aria-hidden="true" />
                  <input
                    ref={patientSearchRef}
                    type="search"
                    placeholder="Search by name, mobile number, or UHID…"
                    value={patientSearch}
                    onChange={(e) => setPatientSearch(e.target.value)}
                    onKeyDown={handleSearchKeyDown}
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
                        {patientResults.map((p, index) => {
                          const age = p.date_of_birth ? formatPreciseAge(p.date_of_birth) : null
                          return (
                            <li key={p.id}>
                              <button
                                type="button"
                                className={
                                  index === highlightIndex
                                    ? 'book-patient-result highlighted'
                                    : 'book-patient-result'
                                }
                                onClick={() => selectPatient(p)}
                              >
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
                    <span className="book-selected-patient-badge">
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
                    <div className="book-selected-patient-identity">
                      <span className="book-patient-avatar" aria-hidden="true">
                        {selectedPatient.name.slice(0, 2).toUpperCase()}
                      </span>
                      <strong className="book-selected-patient-name">{selectedPatient.name}</strong>
                    </div>
                    <div className="muted book-selected-patient-meta">
                      {selectedPatient.uhid}
                      {selectedPatient.date_of_birth ? ` · ${formatDate(selectedPatient.date_of_birth)}` : ''}
                      {selectedPatient.date_of_birth && formatAgeCompact(selectedPatient.date_of_birth)
                        ? ` · ${formatAgeCompact(selectedPatient.date_of_birth)}`
                        : ''}
                      {selectedPatient.gender
                        ? ` · ${selectedPatient.gender.charAt(0)}${selectedPatient.gender.slice(1).toLowerCase()}`
                        : ''}
                    </div>
                    <div className="muted book-selected-patient-meta">{selectedPatient.whatsapp_number}</div>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="book-step-card">
            <div className="book-card-head">
              <h3>Visit type</h3>
            </div>
            <div role="radiogroup" aria-label="Visit type" className="book-type-list">
              {deptTypes.map((t) => {
                const reference = referenceDoctorTypes.find((rt) => rt.id === t.id)
                return (
                  <button
                    key={t.id}
                    type="button"
                    role="radio"
                    aria-checked={String(t.id) === appointmentTypeId}
                    className={String(t.id) === appointmentTypeId ? 'book-type-option active' : 'book-type-option'}
                    disabled={!departmentId}
                    onClick={() => setAppointmentTypeId(String(t.id))}
                  >
                    <span className="book-type-option-name">{t.name}</span>
                    {reference && (
                      <span className="muted book-type-option-meta">
                        {reference.duration_minutes} min · ₹{reference.consultation_fee}
                      </span>
                    )}
                  </button>
                )
              })}
              {deptTypesLoading && <p className="muted">Loading…</p>}
              {!deptTypesLoading && departmentId && deptTypes.length === 0 && (
                <p className="muted">No appointment types are configured for this department yet.</p>
              )}
            </div>
            {/* Duration and fee belong to a (doctor, type) pair, never
                to a type on its own, so these numbers are attributed
                rather than presented as a clinic-wide truth. */}
            {referenceDoctorTypes.length > 0 && bookableDoctors[0] && (
              <p className="muted book-type-note">
                Duration and fee shown for {bookableDoctors[0].name}. Each doctor sets their own.
              </p>
            )}
          </div>
        </div>

        {/* Centre: the soonest offer, then everyone, soonest first. */}
        <div className="book-scheduler-main">
          {appointmentTypeId && nextAvailable && (
            <section className="book-next-available" aria-label="Next available">
              <div className="book-next-available-main">
                <div className="book-next-available-body">
                  <div className="book-next-available-eyebrow">
                    Next available{departmentName ? ` · ${departmentName}` : ''}
                  </div>
                  <div className="book-next-available-headline">
                    <span className="book-next-available-time">{formatTime(nextAvailable.slot.start_at)}</span>
                    {etaFor(nextAvailable.slot) && (
                      <span className="book-next-available-eta">{etaFor(nextAvailable.slot)}</span>
                    )}
                  </div>
                  <div className="book-next-available-doctor">
                    {nextAvailable.doctor.name}
                    {nextAvailable.doctor.specialization ? ` · ${nextAvailable.doctor.specialization}` : ''}
                  </div>
                </div>
                <button
                  type="button"
                  className="book-next-available-cta"
                  disabled={busy || !selectedPatient}
                  onClick={() => handleSubmit({ doctor: nextAvailable.doctor, slot: nextAvailable.slot })}
                >
                  {selectedPatient ? 'Book now' : 'Select a patient first'}
                  <span className="book-key-hint on-dark" aria-hidden="true">Enter ↵</span>
                </button>
              </div>
              {runnerUp && (
                <div className="book-next-available-runner-up">
                  Also soon: {runnerUp.doctor.name} at {formatTime(runnerUp.slot.start_at)}
                  {etaFor(runnerUp.slot) ? ` (${etaFor(runnerUp.slot)})` : ''}
                </div>
              )}
            </section>
          )}

          <div className="book-scheduler-filters">
            <div className="book-scheduler-filter-group book-dept-pills">
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
            {/* A walk-in is here now; there is no other day to put them
                on, so the date is stated and the controls that would
                offer one are not rendered at all. */}
            {isWalkIn ? (
              <span className="book-date-locked">
                <Lock size={14} weight="bold" aria-hidden="true" />
                {dateContext}
              </span>
            ) : (
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
                  <span className="book-date-nav-value">{formatDateWithWeekday(date)}</span>
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
            )}
          </div>

          {/* Slot length is a property of (doctor, visit type), so until
              a type is chosen there is no honest width to draw a chip
              at -- and a fixed grid would show slots that do not fit. */}
          {!appointmentTypeId ? (
            <div className="book-type-required">
              Choose a visit type to see who is free. Slot length and fee depend on it.
            </div>
          ) : (
            <section className="book-doctor-list">
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
              {!gridLoading && !gridError && doctorsWithSlots.length > 0 && (
                <>
                  <div className="book-doctor-list-header">
                    <div>
                      {bookableDoctors.length} {bookableDoctors.length === 1 ? 'doctor' : 'doctors'} available
                      <span className="muted"> · soonest first</span>
                    </div>
                    <div className="book-doctor-list-now">
                      <span className="book-now-dot" aria-hidden="true" />
                      Now {clinicClockLabel(new Date(nowMs))}
                    </div>
                  </div>

                  {bookableDoctors.map((doc, doctorIndex) => {
                    const duration = minutesOfDay(doc.slots[0].end_at) - minutesOfDay(doc.slots[0].start_at)
                    return (
                      <div className="book-doctor-row" key={doc.id}>
                        <div className="book-doctor-row-head">
                          <div className="book-doctor-identity">
                            <span className="book-patient-avatar" aria-hidden="true">
                              {doc.name.replace(/^Dr\.?\s*/i, '').slice(0, 2).toUpperCase()}
                            </span>
                            <div className="book-doctor-naming">
                              <strong>{doc.name}</strong>
                              <span className="muted">
                                {doc.specialization}
                                {duration > 0 ? ` · ${duration}-min slots` : ''}
                              </span>
                            </div>
                          </div>
                          <div className="book-doctor-stats">
                            <span className="book-doctor-next">{formatTime(doc.slots[0].start_at)}</span>
                            <span className="book-doctor-left muted">{doc.slots.length} left</span>
                          </div>
                        </div>
                        <div
                          className="book-doctor-slots"
                          style={{ '--book-slot-col': `${slotColumnWidth(duration)}px` } as React.CSSProperties}
                        >
                          {doc.slots.map((slot, slotIndex) => {
                            const key = slotKey(doc.id, slot)
                            const isSelected = selectedDoctorId === doc.id && selectedSlot?.start_at === slot.start_at
                            return (
                              <button
                                key={key}
                                ref={(el) => {
                                  if (el) cellRefs.current.set(key, el)
                                  else cellRefs.current.delete(key)
                                }}
                                type="button"
                                className={isSelected ? 'book-grid-slot selected' : 'book-grid-slot'}
                                tabIndex={key === effectiveActiveKey ? 0 : -1}
                                title={`${doc.name} · ${formatTime(slot.start_at)}`}
                                aria-label={`${doc.name}, ${formatTime(slot.start_at)}`}
                                aria-pressed={isSelected}
                                onClick={() => pickSlot(doc.id, slot)}
                                onKeyDown={(e) => handleGridKeyDown(e, doctorIndex, slotIndex)}
                              >
                                {formatTime(slot.start_at)}
                              </button>
                            )
                          })}
                        </div>
                      </div>
                    )
                  })}

                  {/* One line each. "Fully booked" and "no clinic" are
                      different facts -- total_slots is the day's
                      capacity, so a doctor with capacity and nothing
                      left is booked out, and a doctor with none simply
                      is not working. */}
                  {unbookableDoctors.map((doc) => (
                    <div className="book-doctor-collapsed" key={doc.id}>
                      <div className="book-doctor-identity">
                        <span className="book-patient-avatar muted-avatar" aria-hidden="true">
                          {doc.name.replace(/^Dr\.?\s*/i, '').slice(0, 2).toUpperCase()}
                        </span>
                        <span className="muted">
                          <strong>{doc.name}</strong>
                          {doc.total_slots > 0 ? ' · fully booked' : ' · not working this day'}
                        </span>
                      </div>
                      <span className="muted book-doctor-collapsed-note">
                        {doc.total_slots > 0 ? `${doc.total_slots} slots, all taken` : 'No clinic scheduled'}
                      </span>
                    </div>
                  ))}
                </>
              )}
            </section>
          )}
        </div>

        {/* Right: exactly what is about to happen. */}
        <div className="book-appointment-side">
          <div className="book-review-card">
            <h3>This appointment</h3>

            <div className="book-summary-rows">
              <div className="book-summary-row book-summary-patient">
                <div className="book-summary-item-label">Patient</div>
                <div className="book-summary-item-value">
                  {selectedPatient ? selectedPatient.name : 'Search or select a patient'}
                </div>
                {selectedPatient && <div className="muted">{selectedPatient.uhid}</div>}
              </div>
              <div className="book-summary-row book-summary-doctor">
                <div className="book-summary-item-label">Doctor</div>
                <div className="book-summary-item-value">
                  {selectedDoctor ? selectedDoctor.name : 'Pick an open slot'}
                </div>
                {selectedDoctor?.specialization && <div className="muted">{selectedDoctor.specialization}</div>}
              </div>
              <div className="book-summary-row book-summary-when">
                <div className="book-summary-item-label">When</div>
                <div className="book-summary-item-value">
                  {selectedSlot ? `${dateContext.split(',')[0]}, ${formatTime(selectedSlot.start_at)}` : 'Not selected'}
                </div>
                <div className="muted">
                  {selectedSlot && etaFor(selectedSlot) ? `${etaFor(selectedSlot)} · ` : ''}
                  {summaryTypeName}
                  {summaryDuration ? `, ${summaryDuration}` : ''}
                </div>
              </div>
              <div className="book-summary-row book-summary-fee">
                <div className="book-summary-item-label">Fee</div>
                <div className="book-summary-item-value">
                  {summaryFee === null ? '—' : `₹${summaryFee}`}
                </div>
              </div>
            </div>

            {/* Walk-in only: both of these are already true of a patient
                standing at the desk, so they are offered here instead
                of as a second trip through the success screen. */}
            {isWalkIn && (
              <div className="book-walkin-toggles">
                <label className="book-checkin-toggle">
                  <input type="checkbox" checked={checkInNow} onChange={(e) => setCheckInNow(e.target.checked)} />
                  Check in now
                </label>
                <label className="book-print-toggle">
                  <input
                    type="checkbox"
                    checked={printTokenSlip}
                    onChange={(e) => setPrintTokenSlip(e.target.checked)}
                  />
                  Print token slip
                </label>
              </div>
            )}

            {!busy && (
              <p className="muted book-review-hint" aria-live="polite">
                {missingSelection ?? 'Ready to book — review the details above, then confirm.'}
              </p>
            )}

            <button type="button" className="btn book-review-cta" disabled={!canSubmit} onClick={() => handleSubmit()}>
              {bookLabel}
            </button>
            <button type="button" className="btn-secondary btn" onClick={resetForm}>
              Cancel
              <span className="book-key-hint" aria-hidden="true">Esc</span>
            </button>
          </div>
        </div>
      </div>

      <div className="book-shortcut-bar">
        <span className="book-shortcut-title">Keyboard</span>
        <span><span className="book-key-hint">/</span> find patient</span>
        <span><span className="book-key-hint">W P O S</span> booking source</span>
        <span><span className="book-key-hint">← → ↑ ↓</span> move between slots</span>
        <span><span className="book-key-hint">↵</span> book</span>
        <span><span className="book-key-hint">Esc</span> clear</span>
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
