import type { DoctorBlockEntry, DoctorScheduleEntry } from '../types'
import { formatTimeOfDay } from '../format'

export const DAY_NAMES = ['', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

// Shared by the Doctors directory (today's-hours/availability column) and
// the doctor workspace's Overview tab, so "what does today look like for
// this doctor" is computed exactly one way, not two drifting copies.

export function isoDateToday(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

// 1=Monday..7=Sunday, matching doctor_schedule.day_of_week (see
// DoctorDetail.tsx's original DAY_NAMES/dayOfWeekOccursInRange) -- the
// opposite of JS's own Date.getDay() (0=Sunday..6=Saturday).
export function currentDayOfWeek(): number {
  const jsDay = new Date().getDay()
  return jsDay === 0 ? 7 : jsDay
}

function dateInRange(dateStr: string, startDate: string | null, endDate: string | null): boolean {
  if (startDate && dateStr < startDate) return false
  if (endDate && dateStr > endDate) return false
  return true
}

// This doctor's recurring weekly schedule rows that apply on `dateStr`
// (day-of-week + date-range match), earliest first.
export function todaysScheduleEntries(
  entries: DoctorScheduleEntry[],
  dateStr: string = isoDateToday(),
  dayOfWeek: number = currentDayOfWeek(),
): DoctorScheduleEntry[] {
  return entries
    .filter((e) => e.active && e.day_of_week === dayOfWeek && dateInRange(dateStr, e.start_date, e.end_date))
    .sort((a, b) => a.start_time.localeCompare(b.start_time))
}

export function formatWorkingHours(entries: DoctorScheduleEntry[]): string[] {
  return entries.map((e) => `${formatTimeOfDay(e.start_time)} – ${formatTimeOfDay(e.end_time)}`)
}

// This calendar week's (Monday-Sunday) working hours, one entry per
// day -- Overview's "This week" summary. Reuses todaysScheduleEntries/
// formatWorkingHours against each of the week's 7 real dates (so a
// schedule row's own start_date/end_date bounds are still respected,
// same as "today"), rather than a separate day-of-week-only pass that
// would ignore date ranges.
export function thisWeekSchedule(
  entries: DoctorScheduleEntry[],
  reference: Date = new Date(),
): { dayOfWeek: number; dateStr: string; hours: string[] }[] {
  const jsDay = reference.getDay()
  const mondayOffset = jsDay === 0 ? -6 : 1 - jsDay
  const monday = new Date(reference)
  monday.setDate(reference.getDate() + mondayOffset)

  const week: { dayOfWeek: number; dateStr: string; hours: string[] }[] = []
  for (let i = 0; i < 7; i++) {
    const date = new Date(monday)
    date.setDate(monday.getDate() + i)
    const dateStr = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    const dayOfWeek = i + 1
    week.push({ dayOfWeek, dateStr, hours: formatWorkingHours(todaysScheduleEntries(entries, dateStr, dayOfWeek)) })
  }
  return week
}

function toMinutesSinceMidnight(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

// Is this doctor inside one of today's working-hour windows right now,
// and not inside an active one-off block? doctor_schedule.start_time/
// end_time have no timezone of their own -- entered and read back as
// plain HH:MM, the same "assume the browser's wall clock is this
// doctor's IST wall clock" convention ScheduleSection's own <input
// type="time"> already relies on. DoctorBlockEntry.start_at/end_at, by
// contrast, are real ISO instants (entered "(IST)" but stored as an
// absolute moment -- see BlocksSection), so comparing those against
// `now.getTime()` is correct regardless of the viewer's own timezone.
export function isAvailableNow(
  scheduleEntries: DoctorScheduleEntry[],
  blocks: DoctorBlockEntry[],
  now: Date = new Date(),
): boolean {
  const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  const dayOfWeek = now.getDay() === 0 ? 7 : now.getDay()
  const nowMinutes = now.getHours() * 60 + now.getMinutes()
  const inWorkingHours = scheduleEntries.some(
    (e) =>
      e.active &&
      e.day_of_week === dayOfWeek &&
      dateInRange(dateStr, e.start_date, e.end_date) &&
      toMinutesSinceMidnight(e.start_time) <= nowMinutes &&
      nowMinutes < toMinutesSinceMidnight(e.end_time),
  )
  if (!inWorkingHours) return false
  const nowInstant = now.getTime()
  const blocked = blocks.some(
    (b) => b.active && new Date(b.start_at).getTime() <= nowInstant && nowInstant < new Date(b.end_at).getTime(),
  )
  return !blocked
}
