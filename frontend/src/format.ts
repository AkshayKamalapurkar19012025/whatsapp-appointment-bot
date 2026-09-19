// Formats an ISO 8601 datetime string (e.g. "2026-09-07T10:00:00+05:30",
// as returned by the backend -- always in the doctor's own local time)
// WITHOUT ever converting it through the browser's local timezone and
// WITHOUT ever displaying the offset or a zone name. Appointment times
// are the clinic's wall-clock time, not "whatever the patient's device
// thinks it is" -- so this reads the hour/minute/date digits straight
// out of the string instead of going through `new Date(...)`, which
// would silently reinterpret them in the viewer's own timezone. This is
// what "do not expose doctor timezone" means in practice: the patient
// sees a correct, plain "10:00 AM" with no zone identifier attached.

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

export function formatTime(isoString: string): string {
  const match = isoString.match(/T(\d{2}):(\d{2}):\d{2}/)
  if (!match) return isoString
  let hour = Number(match[1])
  const minute = match[2]
  const suffix = hour >= 12 ? 'PM' : 'AM'
  hour = hour % 12
  if (hour === 0) hour = 12
  // A non-breaking space keeps "11:30 AM" from splitting across lines
  // in a narrow container -- a regular space there is a legal line-break
  // point, which orphans the AM/PM suffix onto its own line.
  return `${hour}:${minute} ${suffix}`
}

export function formatDate(isoDate: string): string {
  // Accepts either a bare "YYYY-MM-DD" or a full datetime string.
  const match = isoDate.match(/(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return isoDate
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  return `${day} ${MONTH_NAMES[month - 1]} ${year}`
}

export function isoDateOnly(year: number, month: number, day: number): string {
  return `${year}-${pad(month)}-${pad(day)}`
}

// "PT-00124" -- a display-only formatting of the real patients.id
// (OPD Patients-page redesign). Not a new identifier: there is no
// separate UHID column in the database, so this never appears in an
// API request/response, only rendered client-side over the id every
// other patient/appointment record already carries. Zero-padded to 5
// digits purely for a consistent look at low ids; ids beyond that
// width are shown in full rather than truncated.
export function formatPatientId(id: number): string {
  return `PT-${String(id).padStart(5, '0')}`
}

// Zero-padded ISO date ("YYYY-MM-DD") from a JS Date's own
// getFullYear/getMonth/getDate -- shared by parseTypedDob and
// DobPicker.tsx so both build the exact same string shape formatDate
// above already parses (never goes through Date#toISOString, which
// reinterprets through UTC and can shift the date by a day).
export function toIsoDate(d: Date): string {
  return isoDateOnly(d.getFullYear(), d.getMonth() + 1, d.getDate())
}

// Parses a typed "DD/MM/YYYY" (also accepts "-" or "." as the
// separator) into an ISO date, or null if the text isn't a real
// calendar date -- the DOB picker's typed-entry path (OPD Patient
// Search & Registration redesign, point 3: "Allow the receptionist to
// type the date directly," never picker-only). Rejects both malformed
// input (wrong shape, non-numeric) and impossible dates (32/13/2020,
// 31/04/2020, 29/02/2021 -- a non-leap year) via the same round-trip
// check every other robust date parser uses: construct the Date and
// confirm its own y/m/d getters echo back exactly what was typed,
// rather than JS's default behavior of silently rolling an invalid
// date over into the next month. Does NOT reject a future date here --
// that's a business rule (a DOB must be in the past), checked
// separately by callers so this function stays purely "is this a real
// calendar date," not baking in DOB-specific policy.
export function parseTypedDob(text: string): string | null {
  const match = /^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$/.exec(text.trim())
  if (!match) return null
  const day = Number(match[1])
  const month = Number(match[2])
  const year = Number(match[3])
  const d = new Date(year, month - 1, day)
  if (d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day) return null
  return toIsoDate(d)
}

// "18 Sep 1991" -> "18/09/1991", the typed-entry field's own display
// format -- reuses formatDate's day/month/year extraction (same
// raw-digit parsing, no Date/timezone reinterpretation) rather than
// growing a third ad hoc date parser.
export function formatDobForInput(isoDate: string): string {
  const match = isoDate.match(/(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return ''
  return `${match[3]}/${match[2]}/${match[1]}`
}

const GENDER_LABELS: Record<string, string> = { MALE: 'Male', FEMALE: 'Female', OTHER: 'Other' }

// "28 yrs · Male" (OPD Today redesign's patient sub-line) -- both
// date_of_birth and gender are real, already-stored Patient fields
// (migrations/0023, both optional), just never combined into one
// display string before. Age is a plain whole-years calculation from
// the stored date, not a new persisted value. Returns null only when
// NEITHER is set, so a patient with just one of the two still shows
// that one rather than disappearing entirely.
// "35 years" / "2 years 4 months" / "8 months" -- precise age for the
// OPD patient search/registration screens (point 4: age must be
// calculated automatically from DOB, with pediatric precision where it
// matters). Separate from formatAgeGender above rather than changing
// it in place: formatAgeGender's plain whole-years count is already
// used elsewhere in the app (patient list/appointment rows) and
// nothing there asked for month-level precision -- this is additive,
// only used by the new find/register flow.
//
// Under 1 year: months only ("8 months", "0 months" for a newborn this
// same week). 1-2 years: years + months, omitting "0 months" when the
// birthday just passed ("2 years", not "2 years 0 months"). 3+ years:
// plain whole years, same precision formatAgeGender already uses --
// month-level precision stops mattering well before then.
export function formatPreciseAge(dateOfBirth: string): string | null {
  const dob = new Date(`${dateOfBirth}T00:00:00`)
  if (Number.isNaN(dob.getTime())) return null

  const today = new Date()
  if (dob.getTime() > today.getTime()) return null

  let years = today.getFullYear() - dob.getFullYear()
  let months = today.getMonth() - dob.getMonth()
  if (today.getDate() < dob.getDate()) months -= 1
  if (months < 0) {
    years -= 1
    months += 12
  }

  if (years === 0) {
    return `${months} month${months === 1 ? '' : 's'}`
  }
  if (years < 3) {
    return months === 0 ? `${years} year${years === 1 ? '' : 's'}` : `${years} year${years === 1 ? '' : 's'} ${months} month${months === 1 ? '' : 's'}`
  }
  return `${years} year${years === 1 ? '' : 's'}`
}

export function formatAgeGender(dateOfBirth: string | null, gender: string | null): string | null {
  let age: number | null = null
  if (dateOfBirth) {
    const dob = new Date(`${dateOfBirth}T00:00:00`)
    if (!Number.isNaN(dob.getTime())) {
      const today = new Date()
      age = today.getFullYear() - dob.getFullYear()
      const beforeBirthdayThisYear =
        today.getMonth() < dob.getMonth() || (today.getMonth() === dob.getMonth() && today.getDate() < dob.getDate())
      if (beforeBirthdayThisYear) age -= 1
    }
  }
  const genderLabel = gender ? GENDER_LABELS[gender] ?? null : null
  const parts = [age !== null ? `${age} yrs` : null, genderLabel].filter((p): p is string => Boolean(p))
  return parts.length > 0 ? parts.join(' · ') : null
}

// Unlike formatDate/formatTime above, this is for audit-log-style
// timestamps (e.g. "doctor added on") that are NOT a clinic wall-clock
// time -- there's no doctor-local zone to preserve here, just "when did
// this happen" recorded in UTC. Showing it in the viewer's own local
// time (via Date, deliberately unlike the raw-digit approach above) is
// the correct behavior for this case, not a bug.
export function formatDateTime(isoString: string): string {
  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return isoString
  return `${MONTH_NAMES[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`
}

// Same AM/PM conversion as formatTime above, but for a bare "HH:MM" or
// "HH:MM:SS" wall-clock value with no date/timezone attached at all --
// what doctor_schedule.start_time/end_time and the blocks/schedule forms'
// <input type="time"> values actually are. Kept as a separate function
// rather than teaching formatTime a second input shape, since the two
// inputs mean different things (a moment in time vs. a recurring
// time-of-day) even though the digit math is identical.
export function formatTimeOfDay(hhmm: string): string {
  const match = hhmm.match(/^(\d{1,2}):(\d{2})/)
  if (!match) return hhmm
  let hour = Number(match[1])
  const minute = match[2]
  const suffix = hour >= 12 ? 'PM' : 'AM'
  hour = hour % 12
  if (hour === 0) hour = 12
  // Non-breaking space, same reasoning as formatTime above.
  return `${hour}:${minute} ${suffix}`
}

// Unlike formatDate/formatTime above, this genuinely needs `new Date(...)`
// -- not for display (nothing here is shown to a user), just to compare
// two instants. An ISO string carrying its own offset (e.g.
// "2026-09-07T09:45:00+05:30", exactly what the admin appointments
// endpoint returns) always parses to the correct instant regardless of
// the browser's own timezone, so this comparison is safe. This is the
// UI-side half of the "has this appointment started yet" check --
// app/services/appointment_services.py's mark_visited_service enforces
// the same rule server-side (comparing the same instant, not a
// browser-supplied one), so this is a UX nicety, not the real guard.
export function hasStarted(isoString: string): boolean {
  const start = new Date(isoString)
  if (Number.isNaN(start.getTime())) return false
  return start.getTime() <= Date.now()
}

export interface ArrivalDisplay {
  label: string
  sub?: string
  className: string
}

// One shared derivation of "what should this appointment row's status
// pill say" for the arrival-workflow states that plain a.status can't
// express on its own -- used by AppointmentsPanel's table/mobile card,
// DoctorWorkspace, and QueueSection alike, so the label logic never
// drifts into three near-identical copies.
//
// Deliberately reads nothing that isn't already on the appointment:
// arrived_at/start_at/status (migrations/0023's arrived_at, distinct
// from visited_at -- see mark_arrived_service) and payment_status/
// token_number (already existed). Returns null when the plain status
// pill is exactly right as-is (every status except CONFIRMED-with-
// arrived_at and CHECKED_IN, where payment/queue state adds real
// information the bare word "Checked in" doesn't carry).
//
// Never claims a CHECKED_IN appointment without a token is "waiting on
// payment" -- that specific wording is used ONLY when payment_status
// is actually UNPAID; a FAILED attempt says so explicitly instead, and
// a PAID/WAIVED appointment always has a token by the time this is
// read (both record_payment_service and waive_consultation_fee_service
// generate it atomically in the same transaction as the payment_status
// write), so token_number is checked first, before payment_status.
export function describeArrival(
  a: {
    status: string
    arrived_at?: string | null
    start_at: string
    payment_status?: string | null
    token_number?: number | null
  },
): ArrivalDisplay | null {
  if (a.status === 'CONFIRMED' && a.arrived_at) {
    if (!hasStarted(a.start_at)) {
      return {
        label: 'Arrived early',
        sub: `Appointment at ${formatTime(a.start_at)}`,
        className: 'pill status-arrived-early',
      }
    }
    return {
      label: 'Arrived',
      sub: 'Ready to check in',
      className: 'pill status-arrived',
    }
  }

  if (a.status === 'CHECKED_IN') {
    if (a.token_number != null) {
      return {
        label: `Queue Token #${a.token_number}`,
        className: 'pill status-queued',
      }
    }
    if (a.payment_status === 'FAILED') {
      return { label: 'Checked in', sub: 'Payment failed', className: 'pill status-checked_in' }
    }
    if (a.payment_status === 'UNPAID') {
      return { label: 'Checked in', sub: 'Awaiting payment', className: 'pill status-checked_in' }
    }
  }

  return null
}

// "N years experience · MBBS, MD (Cardiology)" -- the one-line summary
// shown on every compact doctor card (DoctorCard.tsx's patient-facing
// selection cards, and the admin Doctors grid), so both places read
// the same two fields the same way instead of two near-identical
// inline implementations drifting apart. null/empty parts are dropped,
// and the whole thing is null (render nothing) if neither is set.
export function doctorSummaryLine(doctor: {
  years_of_experience: number | null
  qualifications: string | null
}): string | null {
  const parts = [
    doctor.years_of_experience !== null
      ? `${doctor.years_of_experience} ${doctor.years_of_experience === 1 ? 'year' : 'years'} experience`
      : null,
    doctor.qualifications,
  ].filter((part): part is string => Boolean(part))
  return parts.length > 0 ? parts.join(' · ') : null
}

// "MBBS · MD · DM" -- the doctor's qualifications, derived from their
// Education & Credentials entries (DoctorProfileSection.tsx) rather
// than a second, separately-typed free-text field. doctors.qualifications
// (DoctorProfileSummary's own field, used by doctorSummaryLine above
// and every compact card that isn't the full profile page) stays as
// the one already-existing column/API field this gets *written into*
// whenever education changes, so those other, unrelated call sites
// keep reading a real value with no API change -- this function is
// just "what should that value be", computed from the one real source
// of truth (education), not a second place to type it by hand.
export function qualificationsFromEducation(education: { qualification: string }[]): string {
  return education.map((e) => e.qualification).join(' · ')
}

// "+91 ******3210" -- masks a "+91XXXXXXXXXX" number (PhoneInput's shape)
// for display on the OTP screen, leaving only the last 4 digits visible.
// Falls back to the raw value unmasked for anything that doesn't match
// that shape (e.g. mid-typing, or before a number has been entered at
// all), rather than showing a misleading mask over data it doesn't
// actually recognize.
export function maskPhone(fullNumber: string): string {
  const match = /^\+91(\d{10})$/.exec(fullNumber)
  if (!match) return fullNumber
  const digits = match[1]
  return `+91 ${'*'.repeat(6)}${digits.slice(6)}`
}

export type AvailabilityLevel = 'red' | 'orange' | 'green' | 'none'

export interface Availability {
  level: AvailabilityLevel
  label: string
  sub?: string
}

// Shared by both scheduling flows (Doctor-First's Slot-step banner and
// Date-First's "Available Doctors" cards) -- one rule, not two: a count
// on its own says nothing about how booked-up a doctor's day is (3 of
// 4 total slots is nearly full; 3 of 40 is wide open), so the color is
// remaining/total, not the raw count. count is clamped at 0 first --
// the underlying slot list can never actually be negative (see
// get_available_slots's count_total docstring), but this is the single
// place that guards against ever rendering a misleading negative
// number if that ever changes.
export function formatAvailability(count: number, total: number): Availability {
  const remaining = Math.max(0, count)

  if (remaining === 0) {
    return {
      level: 'none',
      label: 'No slots available today',
      sub: 'Try another doctor or choose a different date',
    }
  }

  // A floor, not just a ratio: "1 left" reads as urgent regardless of
  // how large the doctor's total day is (1 of 40 and 1 of 2 both mean
  // the same thing to a patient deciding right now), so this is red
  // before the ratio thresholds below ever get a say.
  if (remaining === 1) {
    return { level: 'red', label: '1 slot remaining' }
  }

  const label = `${remaining} slots available`
  const ratio = total > 0 ? remaining / total : 1

  if (ratio <= 0.25) return { level: 'red', label }
  if (ratio <= 0.6) return { level: 'orange', label }
  return { level: 'green', label }
}
