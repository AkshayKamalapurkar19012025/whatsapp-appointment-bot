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
