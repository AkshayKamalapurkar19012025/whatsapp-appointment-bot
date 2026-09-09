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

// Same 1=Monday..7=Sunday convention as currentDayOfWeek, for an
// arbitrary "YYYY-MM-DD" date rather than always today -- the Schedule
// tab's "Generated slots preview" date picker.
export function dateToDayOfWeek(dateStr: string): number {
  const jsDay = new Date(`${dateStr}T00:00:00`).getDay()
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

// A generic, capacity-only slot preview for a given date (Schedule tab's
// "Generated slots preview" panel) -- divides each of that date's
// working-hour windows into `durationMinutes` chunks separated by
// `bufferMinutes`, using the doctor's own default_duration_minutes/
// buffer_minutes (migrations/0022_doctor_slot_settings.sql) unless the
// caller overrides them. Deliberately NOT the same thing as real
// booking availability (app/services/availability_engine.get_available_
// slots, which is per appointment-type and excludes blocks/existing
// appointments) -- this is a quick "how many slots would this shape of
// day produce" preview, not tied to any one appointment type, so it
// doesn't need a network round trip. Real per-type availability is
// still what BookAppointmentPanel's AdminSlotPicker shows.
export function generateDaySlots(
  entries: DoctorScheduleEntry[],
  dateStr: string,
  dayOfWeek: number,
  durationMinutes: number,
  bufferMinutes: number,
): string[] {
  const todays = todaysScheduleEntries(entries, dateStr, dayOfWeek)
  const slots: string[] = []
  for (const entry of todays) {
    let current = toMinutesSinceMidnight(entry.start_time)
    const end = toMinutesSinceMidnight(entry.end_time)
    while (current + durationMinutes <= end) {
      slots.push(minutesToTimeOfDay(current))
      current += durationMinutes + bufferMinutes
    }
  }
  return slots
}

function minutesToTimeOfDay(totalMinutes: number): string {
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  return formatTimeOfDay(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
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
