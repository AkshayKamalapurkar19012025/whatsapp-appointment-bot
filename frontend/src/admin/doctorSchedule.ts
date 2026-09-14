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

// Exported for the Schedule tab's monthly summary view (ScheduleGrid.tsx)
// -- it needs the same "does this row apply on this real calendar date"
// test todaysScheduleEntries already does, but per ScheduleBlock (day +
// date-range, not yet expanded to rows) rather than per persisted row.
export function dateInRange(dateStr: string, startDate: string | null, endDate: string | null): boolean {
  if (startDate && dateStr < startDate) return false
  if (endDate && dateStr > endDate) return false
  return true
}

// Does a one-off DoctorBlockEntry (start_at/end_at -- see its own
// docstring in types.ts: entered/shown in Asia/Kolkata terms, the only
// timezone any doctor here has) cover any part of the given "YYYY-MM-DD"
// calendar date? Same wall-clock assumption isAvailableNow already makes
// for "is the doctor blocked right now" -- this is the whole-day version,
// for the monthly view's "Time off" status.
export function blockCoversDate(block: DoctorBlockEntry, dateStr: string): boolean {
  if (!block.active) return false
  const dayStart = new Date(`${dateStr}T00:00:00`).getTime()
  const dayEnd = new Date(`${dateStr}T23:59:59.999`).getTime()
  const blockStart = new Date(block.start_at).getTime()
  const blockEnd = new Date(block.end_at).getTime()
  return blockStart <= dayEnd && blockEnd >= dayStart
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
  return slotsForEntries(todaysScheduleEntries(entries, dateStr, dayOfWeek), durationMinutes, bufferMinutes)
}

// Shared by generateDaySlots (a real calendar date) and the Schedule
// grid's template-week preview (no calendar date, just "what would a
// day shaped like this produce") -- the caller decides which entries
// apply, this just walks them.
export function slotsForEntries(
  dayEntries: DoctorScheduleEntry[],
  durationMinutes: number,
  bufferMinutes: number,
): string[] {
  const slots: string[] = []
  for (const entry of dayEntries) {
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
  return formatTimeOfDay(minutesToHHMM(totalMinutes))
}

export function minutesToHHMM(totalMinutes: number): string {
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

export function toMinutesSinceMidnight(hhmm: string): number {
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

// ===========================================================================
// Schedule grid (drag-to-block weekly editor) -- doctor_schedule rows grouped
// into visual "blocks" (one contiguous shift, optionally carved with break
// gaps) for the grid to draw/drag/edit, plus the inverse (block -> rows) for
// Save. See migrations/0006 (date ranges) and 0010 (department) -- a block's
// day/department/date-range together are exactly what a persisted row group
// is keyed by; there is still no "break" or "block" concept on the backend.
// ===========================================================================

// Does `dayOfWeek` (1=Monday..7=Sunday) occur at least once between
// startDate and endDate (inclusive)? A schedule row bounded to a date
// range that never actually contains its own day-of-week can be created
// today with no error and then silently never produce a single bookable
// day -- e.g. "Monday, 10 Sep - 11 Sep" when the 10th and 11th are a
// Wednesday and Thursday. An open-ended side (no start or no end) always
// eventually reaches every weekday, so only a fully-bounded range needs
// checking.
export function dayOfWeekOccursInRange(dayOfWeek: number, startDate: string, endDate: string): boolean {
  const start = new Date(`${startDate}T00:00:00`)
  const end = new Date(`${endDate}T00:00:00`)
  if (start > end) return true // a different problem (end before start) -- not this check's job
  const jsTargetDay = dayOfWeek % 7 // Date.getDay(): Sunday=0..Saturday=6; ours: Monday=1..Sunday=7
  const cursor = new Date(start)
  while (cursor <= end) {
    if (cursor.getDay() === jsTargetDay) return true
    cursor.setDate(cursor.getDate() + 1)
  }
  return false
}

export const DURATION_OPTIONS = [15, 20, 30, 45, 60, 90]

// Default "fold this gap into a break rather than two separate blocks"
// cutoff for mergeEntriesIntoBlocks, used the first time the grid loads
// for a doctor with no saved preference yet (see ScheduleGrid's own
// localStorage read/write of this value -- user-adjustable per the PLAN,
// not a fixed constant).
export const DEFAULT_MERGE_GAP_MINUTES = 240

export interface ScheduleBreak {
  start: string
  end: string
}

// Sensible default midpoint break span for a newly added break inside
// [startTime, endTime) -- shared by the weekly grid's per-block panel and
// the monthly day panel's per-period editor so "+ Add a break" behaves
// identically in both.
export function defaultBreakFor(startTime: string, endTime: string): ScheduleBreak {
  const startMin = toMinutesSinceMidnight(startTime)
  const endMin = toMinutesSinceMidnight(endTime)
  const mid = Math.round((startMin + endMin) / 2 / 5) * 5
  return {
    start: minutesToHHMM(Math.max(startMin + 5, mid - 15)),
    end: minutesToHHMM(Math.min(endMin - 5, mid + 15)),
  }
}

// Default working hours for a newly added period. Just the start/end
// time -- the period's scope (which weekday(s), one date or recurring)
// is chosen explicitly by the admin via the Add Schedule flow
// (AddScheduleWizard in ScheduleMonthView.tsx), never defaulted silently.
export function newPeriodDefaults(afterEndTime?: string): { startTime: string; endTime: string } {
  if (!afterEndTime) return { startTime: '09:00', endTime: '17:00' }
  const afterMin = toMinutesSinceMidnight(afterEndTime)
  const startMin = Math.min(afterMin + 60, 22 * 60)
  const endMin = Math.min(startMin + 180, 24 * 60)
  return { startTime: minutesToHHMM(startMin), endTime: minutesToHHMM(endMin) }
}

// "YYYY-MM-DD" +/- deltaDays, for splitBlockForEdit's past/future
// boundaries.
export function shiftDateStr(dateStr: string, deltaDays: number): string {
  const d = new Date(`${dateStr}T00:00:00`)
  d.setDate(d.getDate() + deltaDays)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// How many real calendar dates a set of weekdays within [startDate,
// endDate] actually lands on -- the Add Schedule flow's "this applies to
// N dates" summary (an explicit, honest number instead of a vague
// "recurring"). Returns null for an open-ended side (no finite count to
// give); callers show "every <weekday(s)>" instead in that case.
export function countOccurrences(weekdays: number[], startDate: string | null, endDate: string | null): number | null {
  if (!startDate || !endDate) return null
  return enumerateOccurrences(weekdays, startDate, endDate).length
}

// The actual calendar dates (not just the count) a set of weekdays
// lands on within [startDate, endDate] -- Duplicate Schedule's own
// per-date conflict check and affected-dates list need the dates
// themselves, not just how many there are.
export function enumerateOccurrences(weekdays: number[], startDate: string, endDate: string): string[] {
  const wanted = new Set(weekdays)
  const dates: string[] = []
  const cursor = new Date(`${startDate}T00:00:00`)
  const end = new Date(`${endDate}T00:00:00`)
  while (cursor <= end) {
    const jsDay = cursor.getDay() === 0 ? 7 : cursor.getDay()
    if (wanted.has(jsDay)) {
      dates.push(`${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}-${String(cursor.getDate()).padStart(2, '0')}`)
    }
    cursor.setDate(cursor.getDate() + 1)
  }
  return dates
}

// First real calendar date on/after `fromDate` (and not after `endDate`,
// when bounded) that falls on one of `weekdays` -- the Configure Schedule
// modal's Step 3 "Generated Slots Preview" default date selection, so the
// preview never opens on a date the new schedule wouldn't actually apply
// to. Searches at most a year forward to stay finite for a bounded range
// that (by mistake) never actually contains its own weekday.
export function firstMatchingDate(weekdays: number[], fromDate: string, endDate: string | null): string | null {
  const wanted = new Set(weekdays)
  const cursor = new Date(`${fromDate}T00:00:00`)
  const end = endDate ? new Date(`${endDate}T00:00:00`) : null
  const limit = new Date(cursor)
  limit.setDate(limit.getDate() + 366)
  while (!end || cursor <= end) {
    if (cursor > limit) return null
    const jsDay = cursor.getDay() === 0 ? 7 : cursor.getDay()
    if (wanted.has(jsDay)) {
      return `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}-${String(cursor.getDate()).padStart(2, '0')}`
    }
    cursor.setDate(cursor.getDate() + 1)
  }
  return null
}

export type EditScope = 'this_date' | 'this_and_future' | 'entire'

// The set of a date's ScheduleBlocks that share the exact same
// day-of-week + date-range -- i.e. one coherent, persisted schedule
// "instance" (possibly several working periods, e.g. a split shift),
// as opposed to a second, independently-scoped block that happens to
// also apply to the same real calendar date (see editGroupForDate).
function rangeGroupKey(b: ScheduleBlock): string {
  return rangeKey(b.startDate, b.endDate)
}

// All of a date's applicable blocks (day-of-week + date-range match),
// earliest start time first -- the calendar cell's own "what does this
// date look like" read, shared so the Configure Schedule popup's edit
// target is computed the same way the cell's own display is.
export function applicableBlocksForDate(blocks: ScheduleBlock[], dateStr: string): ScheduleBlock[] {
  const dayOfWeek = dateToDayOfWeek(dateStr)
  return blocks
    .filter((b) => b.day === dayOfWeek && dateInRange(dateStr, b.startDate, b.endDate))
    .sort((a, b) => a.startTime.localeCompare(b.startTime))
}

// The one range-group the Configure Schedule popup edits when a date is
// clicked -- the first (earliest-starting) group found among that
// date's applicable blocks, same "first distinct range wins" precedent
// this codebase already used for Copy Schedule. A date very occasionally
// carries a second, independently-scoped block (e.g. a recurring 9-5
// plus a one-off evening event on just one date) -- that second block
// still renders correctly on the calendar, it's just not what a click
// on that date edits; a deliberate, documented simplification rather
// than a full multi-group editor.
export function editGroupForDate(blocks: ScheduleBlock[], dateStr: string): ScheduleBlock[] {
  const applicable = applicableBlocksForDate(blocks, dateStr)
  if (applicable.length === 0) return []
  const primaryKey = rangeGroupKey(applicable[0])
  return applicable.filter((b) => rangeGroupKey(b) === primaryKey)
}

// Is editing this group from `dateStr` actually ambiguous -- does it
// already span more than the single date it's being edited from? Only
// then does "how far should this change reach" need asking; a group
// that's brand new (no sourceIds) or already scoped to just this one
// date has only one possible answer, so the popup skips the question.
export function groupIsAmbiguous(group: ScheduleBlock[], dateStr: string): boolean {
  if (group.length === 0) return false
  const hasPersisted = group.some((b) => b.sourceIds.length > 0)
  const alreadyJustThisDate = group[0].startDate === dateStr && group[0].endDate === dateStr
  return hasPersisted && !alreadyJustThisDate
}

// Applies an edit (or a removal, when replacement is null) to an
// existing range-group that spans more than one real calendar date,
// honoring the admin's explicit choice of how far the change should
// reach:
//   'this_date'        -- only anchorDate changes; the rest of the
//                          pattern (before and after) keeps its old
//                          shape, via up to two unedited replacement
//                          segments plus the new one-off segment(s).
//   'this_and_future'   -- anchorDate onward gets the edit; everything
//                          strictly before it is unaffected.
//   'entire'            -- the whole group is edited/removed as one.
// `replacement` is the new list of working periods (already shaped as
// ScheduleBlock, day/department already set) the admin configured in
// Step 2 -- this function only decides their startDate/endDate per the
// chosen scope, plus what (if anything) survives from the original
// group outside that scope. Every output is a plain ScheduleBlock with
// the same day/startDate/endDate/department_id shape the backend
// already understands -- no new persisted concept, just more rows (or
// fewer, for a partial removal). Never mutates `originalGroup`;
// sourceIds is cleared on every output since the actual Save is a
// direct delete-the-old-rows/create-the-new-rows call, not a diff.
export function splitGroupForEdit(
  originalGroup: ScheduleBlock[],
  anchorDate: string,
  scope: EditScope,
  replacement: ScheduleBlock[] | null,
): ScheduleBlock[] {
  if (originalGroup.length === 0) return replacement ?? []
  const groupStart = originalGroup[0].startDate
  const groupEnd = originalGroup[0].endDate

  function cloneGroupWithDates(startDate: string | null, endDate: string | null): ScheduleBlock[] {
    return originalGroup.map((b) => ({ ...b, key: newBlockKey(), startDate, endDate, sourceIds: [] }))
  }
  function replacementWithDates(startDate: string | null, endDate: string | null): ScheduleBlock[] {
    if (!replacement) return []
    return replacement.map((b) => ({ ...b, key: newBlockKey(), startDate, endDate, sourceIds: [] }))
  }

  if (scope === 'entire') {
    return replacementWithDates(groupStart, groupEnd)
  }

  const pastNeeded = groupStart === null || groupStart < anchorDate
  const pastBlocks = pastNeeded ? cloneGroupWithDates(groupStart, shiftDateStr(anchorDate, -1)) : []

  if (scope === 'this_and_future') {
    return [...pastBlocks, ...replacementWithDates(anchorDate, groupEnd)]
  }

  // 'this_date'
  const futureNeeded = groupEnd === null || anchorDate < groupEnd
  const futureBlocks = futureNeeded ? cloneGroupWithDates(shiftDateStr(anchorDate, 1), groupEnd) : []
  return [...pastBlocks, ...futureBlocks, ...replacementWithDates(anchorDate, anchorDate)]
}

// One drawable/editable shift on the grid -- corresponds to one or more
// doctor_schedule rows (sourceIds; empty for a block drawn in this
// session that has never been saved). A block with breaks expands back
// to N rows at Save time (blockSegments), exactly mirroring the old
// Working Hours form's "hours split around zero or more breaks" model.
export interface ScheduleBlock {
  key: string
  day: number
  startTime: string
  endTime: string
  breaks: ScheduleBreak[]
  departmentId: number | null
  startDate: string | null
  endDate: string | null
  sourceIds: number[]
}

let blockKeySeq = 0
export function newBlockKey(): string {
  blockKeySeq += 1
  return `blk-${Date.now()}-${blockKeySeq}`
}

export function rangeKey(startDate: string | null, endDate: string | null): string {
  return `${startDate ?? ''}|${endDate ?? ''}`
}

// Identifies a doctor_schedule row by its content (not id) -- lets Save
// diff "what the draft now wants" against "what's actually persisted"
// as plain set membership, since an untouched block's blockSegments()
// output is defined to exactly reproduce the rows mergeEntriesIntoBlocks
// built it from.
export function rowTupleKey(row: {
  day_of_week: number
  start_time: string
  end_time: string
  start_date: string | null
  end_date: string | null
  department_id: number | null
}): string {
  return `${row.day_of_week}|${row.start_time}|${row.end_time}|${row.start_date ?? ''}|${row.end_date ?? ''}|${row.department_id ?? ''}`
}

// The same breaks-within-bounds validation the old Working Hours form's
// validateBreaks() did (end>start, within the shift's own bounds,
// non-overlapping) -- reused as-is by the grid's break-carving UI so
// there is exactly one implementation of "is this a legal set of breaks".
export function validateBlockBreaks(startTime: string, endTime: string, breaks: ScheduleBreak[]): string | null {
  const sorted = [...breaks].sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0))
  for (const b of sorted) {
    if (!(b.start < b.end)) return "Each break's end time must be after its start time"
    if (!(startTime < b.start) || !(b.end < endTime)) {
      return 'Breaks must fall entirely within the shift'
    }
  }
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].start < sorted[i - 1].end) {
      return 'Breaks cannot overlap each other'
    }
  }
  return null
}

// Configure Schedule Step 2's full validation for a day's working
// periods -- each period's own start/end/breaks (reusing
// validateBlockBreaks, so there's still exactly one "is this a legal
// break" rule), plus a check the old per-period reduce didn't do: two
// working periods (split shifts) must not overlap each other. Named per
// period ("Period 2 overlaps Period 1") so the message is useful without
// the admin having to guess which one is the problem.
export function validatePeriods(periods: { startTime: string; endTime: string; breaks: ScheduleBreak[] }[]): string | null {
  for (let i = 0; i < periods.length; i++) {
    const p = periods[i]
    if (!(p.startTime < p.endTime)) return `Period ${i + 1}’s end time must be after its start time`
    const breaksError = validateBlockBreaks(p.startTime, p.endTime, p.breaks)
    if (breaksError) return `Period ${i + 1}: ${breaksError}`
  }
  const byStart = periods.map((p, i) => ({ ...p, i })).sort((a, b) => (a.startTime < b.startTime ? -1 : a.startTime > b.startTime ? 1 : 0))
  for (let k = 1; k < byStart.length; k++) {
    if (byStart[k].startTime < byStart[k - 1].endTime) {
      return `Period ${byStart[k].i + 1} overlaps Period ${byStart[k - 1].i + 1}`
    }
  }
  return null
}

// A block's working hours split around zero or more sorted,
// non-overlapping breaks -- e.g. 09:00-17:00 with breaks at 11:00-11:15
// and 13:00-14:00 becomes [09:00-11:00, 11:15-13:00, 14:00-17:00]. With
// no breaks this is just the one [startTime, endTime] segment. This is
// the shape create/update doctor_schedule actually persists.
export function blockSegments(
  startTime: string,
  endTime: string,
  breaks: ScheduleBreak[],
): { start: string; end: string }[] {
  const sorted = [...breaks].sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0))
  const segments: { start: string; end: string }[] = []
  let cursor = startTime
  for (const b of sorted) {
    segments.push({ start: cursor, end: b.start })
    cursor = b.end
  }
  segments.push({ start: cursor, end: endTime })
  return segments
}

// Groups a doctor's persisted (or draft) rows into grid blocks: same
// day + department + date-range rows, sorted by start time, folded into
// one block-with-breaks wherever the gap between consecutive rows is <=
// thresholdMinutes (a doctor-adjustable "does this read as a break or as
// two separate shifts" cutoff -- see DEFAULT_MERGE_GAP_MINUTES). A gap
// bigger than the threshold starts a new, independent block instead of
// stretching one block across a implausibly long "break". This is a
// display/editing inference only -- it never mutates what's persisted;
// an unedited block re-expands to the exact same rows at Save time.
export function mergeEntriesIntoBlocks(entries: DoctorScheduleEntry[], thresholdMinutes: number): ScheduleBlock[] {
  const groups = new Map<string, DoctorScheduleEntry[]>()
  for (const e of entries) {
    if (!e.active) continue
    const key = `${e.day_of_week}|${e.department_id ?? ''}|${rangeKey(e.start_date, e.end_date)}`
    const group = groups.get(key)
    if (group) group.push(e)
    else groups.set(key, [e])
  }

  const blocks: ScheduleBlock[] = []
  for (const group of groups.values()) {
    const sorted = [...group].sort((a, b) => a.start_time.localeCompare(b.start_time))
    let current: ScheduleBlock | null = null
    for (const row of sorted) {
      if (!current) {
        current = {
          key: newBlockKey(),
          day: row.day_of_week,
          startTime: row.start_time,
          endTime: row.end_time,
          breaks: [],
          departmentId: row.department_id,
          startDate: row.start_date,
          endDate: row.end_date,
          sourceIds: [row.id],
        }
        continue
      }
      const gap = toMinutesSinceMidnight(row.start_time) - toMinutesSinceMidnight(current.endTime)
      if (gap <= thresholdMinutes) {
        if (gap > 0) current.breaks.push({ start: current.endTime, end: row.start_time })
        current.endTime = row.end_time
        current.sourceIds.push(row.id)
      } else {
        blocks.push(current)
        current = {
          key: newBlockKey(),
          day: row.day_of_week,
          startTime: row.start_time,
          endTime: row.end_time,
          breaks: [],
          departmentId: row.department_id,
          startDate: row.start_date,
          endDate: row.end_date,
          sourceIds: [row.id],
        }
      }
    }
    if (current) blocks.push(current)
  }
  return blocks.sort((a, b) => a.day - b.day || a.startTime.localeCompare(b.startTime))
}

// Inverse of mergeEntriesIntoBlocks -- the payload shape Save posts to
// POST /doctors/{id}/schedule, one per (block x segment-around-breaks).
export function blocksToRowPayloads(blocks: ScheduleBlock[]): {
  day_of_week: number
  start_time: string
  end_time: string
  start_date: string | null
  end_date: string | null
  department_id: number | null
}[] {
  return blocks.flatMap((b) =>
    blockSegments(b.startTime, b.endTime, b.breaks).map((seg) => ({
      day_of_week: b.day,
      start_time: seg.start,
      end_time: seg.end,
      start_date: b.startDate,
      end_date: b.endDate,
      department_id: b.departmentId,
    })),
  )
}

// A template-week preview for one day-of-week's currently-drawn blocks --
// ignores date-range filtering entirely (the grid represents "the
// pattern", not any one real calendar date), reusing the exact same
// slotsForEntries the real per-date preview and the old Working Hours
// form both use.
export function templateDaySlots(
  blocksForDay: ScheduleBlock[],
  durationMinutes: number,
  bufferMinutes: number,
): string[] {
  const entries: DoctorScheduleEntry[] = blocksForDay.flatMap((b) =>
    blockSegments(b.startTime, b.endTime, b.breaks).map((seg, i) => ({
      id: -1000 - i,
      day_of_week: b.day,
      start_time: seg.start,
      end_time: seg.end,
      active: true,
      start_date: null,
      end_date: null,
      department_id: b.departmentId,
    })),
  )
  entries.sort((a, b) => a.start_time.localeCompare(b.start_time))
  return slotsForEntries(entries, durationMinutes, bufferMinutes)
}

export type TimelineSegmentType = 'slot' | 'break' | 'gap'

export interface TimelineSegment {
  type: TimelineSegmentType
  start: string // HH:MM
  end: string // HH:MM
}

// The same per-day slot generation as templateDaySlots (one pure walk
// over each working sub-segment at durationMinutes+bufferMinutes steps),
// but returning the whole day's shape instead of just the bookable
// starts: explicit breaks and the gap between two separate working
// periods both come back as their own segment, in chronological order,
// so a timeline can render continuously from the first period's start
// to the last period's end -- a split shift must visibly show the
// doctor as unavailable in between, not look like one continuous 9-5
// availability window. A trailing few minutes inside one working
// sub-segment that don't add up to a whole extra slot are dropped
// silently (not surfaced as their own "not available" segment) -- that
// leftover is a rounding artifact of the chosen duration/buffer, not
// meaningful schedule information the admin needs to see.
export function timelineForBlocks(
  blocksForDay: ScheduleBlock[],
  durationMinutes: number,
  bufferMinutes: number,
): TimelineSegment[] {
  const periods = [...blocksForDay].sort((a, b) => a.startTime.localeCompare(b.startTime))
  const segments: TimelineSegment[] = []
  let cursorEnd: string | null = null

  for (const period of periods) {
    if (cursorEnd !== null && cursorEnd < period.startTime) {
      segments.push({ type: 'gap', start: cursorEnd, end: period.startTime })
    }

    const working = blockSegments(period.startTime, period.endTime, period.breaks)
    const breaksSorted = [...period.breaks].sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0))

    working.forEach((seg, i) => {
      let cursor = toMinutesSinceMidnight(seg.start)
      const segEnd = toMinutesSinceMidnight(seg.end)
      while (cursor + durationMinutes <= segEnd) {
        segments.push({ type: 'slot', start: minutesToHHMM(cursor), end: minutesToHHMM(cursor + durationMinutes) })
        cursor += durationMinutes + bufferMinutes
      }
      if (i < breaksSorted.length) {
        segments.push({ type: 'break', start: breaksSorted[i].start, end: breaksSorted[i].end })
      }
    })

    cursorEnd = period.endTime
  }

  return segments
}

// Step 2's own "what does a day shaped like this look like" preview --
// deliberately NOT slot generation (that's timelineForBlocks/Step 3's
// job, which needs a duration+buffer to walk): just the working periods
// and breaks the admin has entered so far, in chronological order, plus
// the gap between two separate working periods (a split shift) -- reuses
// TimelineSegment's own vocabulary ('slot' standing in for "working"
// here, 'break', 'gap') so this needs no new segment type, just a
// different label in whatever renders it.
export function dailyOverview(periods: { startTime: string; endTime: string; breaks: ScheduleBreak[] }[]): TimelineSegment[] {
  const sorted = [...periods].sort((a, b) => a.startTime.localeCompare(b.startTime))
  const segments: TimelineSegment[] = []
  let cursorEnd: string | null = null
  for (const period of sorted) {
    if (cursorEnd !== null && cursorEnd < period.startTime) {
      segments.push({ type: 'gap', start: cursorEnd, end: period.startTime })
    }
    const breaksSorted = [...period.breaks].sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0))
    let cursor = period.startTime
    for (const br of breaksSorted) {
      segments.push({ type: 'slot', start: cursor, end: br.start })
      segments.push({ type: 'break', start: br.start, end: br.end })
      cursor = br.end
    }
    segments.push({ type: 'slot', start: cursor, end: period.endTime })
    cursorEnd = period.endTime
  }
  return segments
}

export function countSlots(segments: TimelineSegment[]): { total: number; morning: number; afternoon: number } {
  const slotStarts = segments.filter((s) => s.type === 'slot').map((s) => toMinutesSinceMidnight(s.start))
  const morning = slotStarts.filter((m) => m < 12 * 60).length
  return { total: slotStarts.length, morning, afternoon: slotStarts.length - morning }
}

// ===========================================================================
// Schedule conflict / overlap detection -- a frontend-side mirror of the
// backend's own schedule_overlaps() (app/api/doctor_schedule.py): two rows
// conflict only when BOTH their time range and their date range overlap,
// department-agnostic (a doctor can only be in one place at a time). This
// gives Add/Edit/Duplicate Schedule immediate feedback before Save; the
// backend re-checks the same rule authoritatively at persist time.
// ===========================================================================

// Null-aware date-range overlap, matching schedule_overlaps()'s own SQL:
// a NULL start/end is unbounded on that side.
function dateRangesOverlapNullable(
  aStart: string | null,
  aEnd: string | null,
  bStart: string | null,
  bEnd: string | null,
): boolean {
  const startsBeforeBEnds = aStart === null || bEnd === null || aStart <= bEnd
  const endsAfterBStarts = aEnd === null || bStart === null || aEnd >= bStart
  return startsBeforeBEnds && endsAfterBStarts
}

function timeRangesOverlap(aStart: string, aEnd: string, bStart: string, bEnd: string): boolean {
  return aStart < bEnd && aEnd > bStart
}

export interface ScheduleConflictDetail {
  // The real calendar date this conflict was found on, or null when the
  // candidate's own date range is unbounded (no finite date list exists
  // to enumerate -- see checkScheduleConflicts).
  date: string | null
  existingStart: string
  existingEnd: string
  newStart: string
  newEnd: string
  overlapStart: string
  overlapEnd: string
  existingDepartmentId: number | null
}

export interface ScheduleConflictCheck {
  conflicts: ScheduleConflictDetail[]
  conflictDates: Set<string>
  // Total real calendar dates the candidate would occupy, or null when
  // the candidate's own range is unbounded (recurring, no end date).
  totalDates: number | null
}

// Checks one day-of-week's candidate time segments (already split around
// their own breaks, same shape blockSegments/blocksToRowPayloads
// produce) against every OTHER persisted block on that same weekday.
// `excludeSourceIds` is the set of persisted row ids about to be deleted
// as part of this same save (an edit replacing its own group) -- those
// rows are not a real conflict with the thing that's about to replace
// them.
//
// When the candidate's own [startDate, endDate] is fully bounded, every
// real calendar date it lands on is checked individually (so a
// multi-date scope can report exactly which dates conflict and which
// don't). When it's open-ended on either side, there's no finite date
// list -- falls back to the same date-RANGE overlap test the backend
// itself uses, so a recurring schedule still gets a real, if less
// granular, conflict result.
export function checkScheduleConflicts(
  existingBlocks: ScheduleBlock[],
  excludeSourceIds: number[],
  day: number,
  segments: { start: string; end: string }[],
  startDate: string | null,
  endDate: string | null,
): ScheduleConflictCheck {
  const candidates = existingBlocks.filter(
    (b) => b.day === day && !b.sourceIds.some((id) => excludeSourceIds.includes(id)),
  )
  const conflicts: ScheduleConflictDetail[] = []
  const conflictDates = new Set<string>()

  // A ScheduleBlock's own startTime/endTime span a break as if it were
  // scheduled -- the break itself is never actually bookable, so the
  // real "existing schedule" time ranges to compare against are its
  // break-split segments (the same shape blockSegments produces for
  // persistence), not the block's raw span.
  function existingSegmentsFor(block: ScheduleBlock): { start: string; end: string }[] {
    return blockSegments(block.startTime, block.endTime, block.breaks)
  }

  function record(
    date: string | null,
    seg: { start: string; end: string },
    existingSeg: { start: string; end: string },
    existingBlock: ScheduleBlock,
  ) {
    conflicts.push({
      date,
      existingStart: existingSeg.start,
      existingEnd: existingSeg.end,
      newStart: seg.start,
      newEnd: seg.end,
      overlapStart: seg.start > existingSeg.start ? seg.start : existingSeg.start,
      overlapEnd: seg.end < existingSeg.end ? seg.end : existingSeg.end,
      existingDepartmentId: existingBlock.departmentId,
    })
    if (date) conflictDates.add(date)
  }

  if (startDate && endDate) {
    const dates = enumerateOccurrences([day], startDate, endDate)
    for (const date of dates) {
      const applicable = candidates.filter((b) => dateInRange(date, b.startDate, b.endDate))
      for (const seg of segments) {
        for (const existing of applicable) {
          for (const existingSeg of existingSegmentsFor(existing)) {
            if (timeRangesOverlap(seg.start, seg.end, existingSeg.start, existingSeg.end)) {
              record(date, seg, existingSeg, existing)
            }
          }
        }
      }
    }
    return { conflicts, conflictDates, totalDates: dates.length }
  }

  const applicable = candidates.filter((b) => dateRangesOverlapNullable(startDate, endDate, b.startDate, b.endDate))
  for (const seg of segments) {
    for (const existing of applicable) {
      for (const existingSeg of existingSegmentsFor(existing)) {
        if (timeRangesOverlap(seg.start, seg.end, existingSeg.start, existingSeg.end)) {
          record(null, seg, existingSeg, existing)
        }
      }
    }
  }
  return { conflicts, conflictDates, totalDates: null }
}

// Combines several per-weekday checkScheduleConflicts results (a scope
// spanning more than one weekday) into one -- totalDates stays finite
// only when every underlying check was itself finite.
export function mergeConflictChecks(results: ScheduleConflictCheck[]): ScheduleConflictCheck {
  const conflicts = results.flatMap((r) => r.conflicts)
  const conflictDates = new Set<string>()
  let totalDates: number | null = 0
  for (const r of results) {
    for (const d of r.conflictDates) conflictDates.add(d)
    if (r.totalDates === null) totalDates = null
    else if (totalDates !== null) totalDates += r.totalDates
  }
  return { conflicts, conflictDates, totalDates }
}

// ===========================================================================
// Time Off availability preview -- a display-only computation of what a
// one-off block (doctor_blocks) does to a given calendar date's working
// hours, used by the Time Off page's calendar and Add/Edit Time Off
// modal. The backend's own availability_engine.py remains the sole
// authority on real bookable slots (appointment type + duration/buffer +
// existing appointments all factor in there); this only answers "does
// this date read as Working / Partial / Time off / No schedule", the
// same question ScheduleMonthView's calendar already answers for the
// Schedule tab, just extended to distinguish a block that covers a
// working day only PARTLY from one that covers it entirely.
// ===========================================================================

// Unlike doctor_schedule's own start_time/end_time (plain HH:MM with no
// date/zone attached), doctor_blocks.start_at/end_at are TIMESTAMPTZ --
// real instants -- and this backend serializes them back as UTC
// ("...+00:00"), NOT in the +05:30 offset format.ts's formatTime
// docstring assumes (verified live: POSTing a block with an explicit
// +05:30 offset still comes back "+00:00" from GET .../blocks). Reading
// digits straight out of the string like formatTime does would show
// the doctor's UTC time as if it were their IST wall clock, off by 5.5
// hours. This app has no doctor-timezone concept beyond "every doctor
// is Asia/Kolkata" (see DoctorBlockEntry's own docstring in types.ts),
// so converting is a fixed, always-correct +05:30 shift applied to the
// real parsed instant, then read back via the UTC getters (never
// `.getHours()`, which would reinterpret in the *viewer's* zone).
const IST_OFFSET_MINUTES = 5 * 60 + 30

function toIstParts(iso: string): { date: string; hhmm: string } {
  const shifted = new Date(new Date(iso).getTime() + IST_OFFSET_MINUTES * 60_000)
  const y = shifted.getUTCFullYear()
  const m = String(shifted.getUTCMonth() + 1).padStart(2, '0')
  const d = String(shifted.getUTCDate()).padStart(2, '0')
  const hh = String(shifted.getUTCHours()).padStart(2, '0')
  const mm = String(shifted.getUTCMinutes()).padStart(2, '0')
  return { date: `${y}-${m}-${d}`, hhmm: `${hh}:${mm}` }
}

// The doctor's own IST wall-clock HH:MM for a doctor_blocks instant.
export function instantToLocalHHMM(iso: string): string {
  return toIstParts(iso).hhmm
}

// The doctor's own IST wall-clock YYYY-MM-DD for a doctor_blocks instant.
export function instantToLocalDate(iso: string): string {
  return toIstParts(iso).date
}

// The local start/end calendar dates a one-off block spans -- for the
// Upcoming Time Off list's "Sep 25 – 27" grouping and the Edit modal's
// date-range prefill. A single-day block has startDate === endDate.
export function blockLocalDateRange(block: DoctorBlockEntry): { startDate: string; endDate: string } {
  return { startDate: instantToLocalDate(block.start_at), endDate: instantToLocalDate(block.end_at) }
}

// Is this block, on this specific calendar date, exactly a "full day"
// time off (00:00 through end-of-day)? Used to decide the Edit modal's
// Full day/Specific hours default -- a block created via this UI's own
// "Full day" option always saves 00:00-on-the-first-day through
// 23:59-on-the-last-day (see TimeOffModal.tsx), so this only ever comes
// back false for a block someone created with genuinely partial hours.
export function blockIsFullDayOnDate(block: DoctorBlockEntry, dateStr: string): boolean {
  const { startDate, endDate } = blockLocalDateRange(block)
  if (dateStr < startDate || dateStr > endDate) return false
  const startsAtMidnight = dateStr > startDate || instantToLocalHHMM(block.start_at) === '00:00'
  const endsAtDayEnd = dateStr < endDate || instantToLocalHHMM(block.end_at) >= '23:00'
  return startsAtMidnight && endsAtDayEnd
}

// This block's time-off window on one specific calendar date, clipped
// to that date's own 00:00-23:59 span -- null if the block doesn't
// reach this date at all. A block spanning several days (Sep 25-27)
// reads as a full 00:00-23:59 window on every date strictly between its
// own start and end date, and a partial window only on the start/end
// date itself.
function timeOffWindowForDate(block: DoctorBlockEntry, dateStr: string): { start: string; end: string } | null {
  const { startDate, endDate } = blockLocalDateRange(block)
  if (dateStr < startDate || dateStr > endDate) return null
  const start = dateStr === startDate ? instantToLocalHHMM(block.start_at) : '00:00'
  const end = dateStr === endDate ? instantToLocalHHMM(block.end_at) : '23:59'
  return { start, end }
}

export type DayAvailabilityStatus = 'time_off' | 'working' | 'partial' | 'none'

export interface DayAvailabilitySegment {
  type: 'available' | 'time_off'
  start: string
  end: string
}

// The Time Off page's per-date read: this date's working hours (from
// the recurring Doctor Schedule) with any active one-off blocks
// subtracted out, as a chronological list of available/time_off
// segments, plus the single status label the calendar cell shows.
// `workingBlocks` is the date's own applicable ScheduleBlocks (schedule,
// not time off), returned alongside so a caller can show the regular
// schedule's own hours without a second lookup.
export function dayAvailability(
  dateStr: string,
  blocks: ScheduleBlock[],
  oneOffBlocks: DoctorBlockEntry[],
): { status: DayAvailabilityStatus; segments: DayAvailabilitySegment[]; workingBlocks: ScheduleBlock[] } {
  const workingBlocks = applicableBlocksForDate(blocks, dateStr)
  const workingRanges = workingBlocks
    .flatMap((b) => blockSegments(b.startTime, b.endTime, b.breaks))
    .sort((a, b) => a.start.localeCompare(b.start))
  const timeOffRanges = oneOffBlocks
    .filter((b) => b.active)
    .map((b) => timeOffWindowForDate(b, dateStr))
    .filter((r): r is { start: string; end: string } => r !== null)
    .sort((a, b) => a.start.localeCompare(b.start))

  if (workingRanges.length === 0) {
    return { status: timeOffRanges.length > 0 ? 'time_off' : 'none', segments: [], workingBlocks }
  }
  if (timeOffRanges.length === 0) {
    return {
      status: 'working',
      segments: workingRanges.map((r) => ({ type: 'available' as const, ...r })),
      workingBlocks,
    }
  }

  const segments: DayAvailabilitySegment[] = []
  for (const wr of workingRanges) {
    let cursor = wr.start
    const overlapping = timeOffRanges.filter((tr) => tr.start < wr.end && tr.end > wr.start)
    for (const tr of overlapping) {
      const clipStart = tr.start > wr.start ? tr.start : wr.start
      const clipEnd = tr.end < wr.end ? tr.end : wr.end
      if (cursor < clipStart) segments.push({ type: 'available', start: cursor, end: clipStart })
      segments.push({ type: 'time_off', start: clipStart, end: clipEnd })
      cursor = clipEnd > cursor ? clipEnd : cursor
    }
    if (cursor < wr.end) segments.push({ type: 'available', start: cursor, end: wr.end })
  }

  const hasAvailable = segments.some((s) => s.type === 'available')
  const hasTimeOff = segments.some((s) => s.type === 'time_off')
  const status: DayAvailabilityStatus = hasAvailable && hasTimeOff ? 'partial' : hasTimeOff ? 'time_off' : 'working'
  return { status, segments, workingBlocks }
}

// Total minutes across a set of time_off segments -- the Add/Edit Time
// Off modal's "This will block N hours of availability" line.
export function blockedMinutes(segments: DayAvailabilitySegment[]): number {
  return segments
    .filter((s) => s.type === 'time_off')
    .reduce((sum, s) => sum + (toMinutesSinceMidnight(s.end) - toMinutesSinceMidnight(s.start)), 0)
}

// "7 hours" / "45 minutes" / "1h 30m" -- shared by Configure Schedule's
// Preview Summary and the Time Off modal's Availability Impact, so a
// duration reads identically everywhere in this app rather than each
// caller rolling its own pluralization.
export function formatDurationHours(totalMinutes: number): string {
  if (totalMinutes <= 0) return '0 hours'
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  if (m === 0) return `${h} hour${h === 1 ? '' : 's'}`
  if (h === 0) return `${m} minute${m === 1 ? '' : 's'}`
  return `${h}h ${m}m`
}
