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
  return `${hour}:${minute} ${suffix}`
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
  return `${hour}:${minute} ${suffix}`
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
