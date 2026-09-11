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
  const wanted = new Set(weekdays)
  let count = 0
  const cursor = new Date(`${startDate}T00:00:00`)
  const end = new Date(`${endDate}T00:00:00`)
  while (cursor <= end) {
    const jsDay = cursor.getDay() === 0 ? 7 : cursor.getDay()
    if (wanted.has(jsDay)) count++
    cursor.setDate(cursor.getDate() + 1)
  }
  return count
}

export type EditScope = 'this_date' | 'this_and_future' | 'entire'

// Applies an edit (or a removal, when mutator is null) to an existing
// block that spans more than one real calendar date, honoring the
// admin's explicit choice of how far the change should reach:
//   'this_date'        -- only anchorDate changes; the rest of the
//                          pattern (before and after) keeps its old
//                          shape, via up to two unedited replacement
//                          segments plus one new one-off segment.
//   'this_and_future'   -- anchorDate onward gets the edit; everything
//                          strictly before it is unaffected.
//   'entire'            -- the whole block is edited/removed as one,
//                          today's existing behavior.
// Every segment this produces is a plain ScheduleBlock with the same
// day/startDate/endDate/department_id shape the backend already
// understands -- no new persisted concept, just more rows (or fewer,
// for a partial removal). Never mutates `original`; sourceIds is
// cleared on every output since (a) the real Save diff is purely
// tuple-content based (see ScheduleGrid's desiredKeys/existingKeys) so
// sourceIds isn't needed for correctness, and (b) clearing it means a
// second edit to a freshly-split segment in the same sitting is treated
// as an edit to a new/unambiguous block and doesn't re-prompt.
export function splitBlockForEdit(
  original: ScheduleBlock,
  anchorDate: string,
  scope: EditScope,
  mutator: ((b: ScheduleBlock) => ScheduleBlock) | null,
): ScheduleBlock[] {
  if (scope === 'entire') {
    return mutator ? [{ ...mutator(original), key: newBlockKey(), sourceIds: [] }] : []
  }

  const pastNeeded = original.startDate === null || original.startDate < anchorDate
  const pastSegment: ScheduleBlock[] = pastNeeded
    ? [{ ...original, key: newBlockKey(), endDate: shiftDateStr(anchorDate, -1), sourceIds: [] }]
    : []

  if (scope === 'this_and_future') {
    const futureSegment: ScheduleBlock[] = mutator
      ? [{ ...mutator(original), key: newBlockKey(), startDate: anchorDate, sourceIds: [] }]
      : []
    return [...pastSegment, ...futureSegment]
  }

  // 'this_date'
  const futureNeeded = original.endDate === null || anchorDate < original.endDate
  const futureSegment: ScheduleBlock[] = futureNeeded
    ? [{ ...original, key: newBlockKey(), startDate: shiftDateStr(anchorDate, 1), sourceIds: [] }]
    : []
  const thisDateSegment: ScheduleBlock[] = mutator
    ? [{ ...mutator(original), key: newBlockKey(), startDate: anchorDate, endDate: anchorDate, sourceIds: [] }]
    : []
  return [...pastSegment, ...futureSegment, ...thisDateSegment]
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

export function parseRangeKey(key: string): { startDate: string | null; endDate: string | null } {
  const [s, e] = key.split('|')
  return { startDate: s || null, endDate: e || null }
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

// Strict overlap (unlike blocksOverlapOrTouch below): two blocks that
// merely touch end-to-end are NOT a conflict for copyBlockToDay -- only
// paint/erase treat touching as "the same drawn stroke".
function blocksStrictlyOverlap(a: { startTime: string; endTime: string }, startTime: string, endTime: string): boolean {
  return toMinutesSinceMidnight(startTime) < toMinutesSinceMidnight(a.endTime) &&
    toMinutesSinceMidnight(endTime) > toMinutesSinceMidnight(a.startTime)
}

// "Copy schedule": places a copy of one day's block (its exact times,
// breaks, and department) onto a different day/range, skipping it
// silently if it would overlap a block already there on that day+range
// -- same "skip conflicting days, don't abort the whole copy" behavior
// the old Working Hours form's own copy feature had, just now staged
// into the draft (part of the one save/cancel lifecycle) instead of
// persisted immediately. Real overlap validation still happens at Save
// time (schedule_overlaps() server-side); this is a good-enough
// pre-check so an obviously-conflicting copy doesn't even make it into
// the draft.
export function copyBlockToDay(
  blocks: ScheduleBlock[],
  targetDay: number,
  source: { startTime: string; endTime: string; breaks: ScheduleBreak[]; departmentId: number | null },
  startDate: string | null,
  endDate: string | null,
): { blocks: ScheduleBlock[]; copied: boolean } {
  const targetRangeKey = rangeKey(startDate, endDate)
  const conflict = blocks.some(
    (b) =>
      b.day === targetDay &&
      rangeKey(b.startDate, b.endDate) === targetRangeKey &&
      blocksStrictlyOverlap(b, source.startTime, source.endTime),
  )
  if (conflict) return { blocks, copied: false }
  const copy: ScheduleBlock = {
    key: newBlockKey(),
    day: targetDay,
    startTime: source.startTime,
    endTime: source.endTime,
    breaks: source.breaks,
    departmentId: source.departmentId,
    startDate,
    endDate,
    sourceIds: [],
  }
  return { blocks: [...blocks, copy], copied: true }
}

function blocksOverlapOrTouch(a: { startTime: string; endTime: string }, startTime: string, endTime: string): boolean {
  return toMinutesSinceMidnight(startTime) <= toMinutesSinceMidnight(a.endTime) &&
    toMinutesSinceMidnight(endTime) >= toMinutesSinceMidnight(a.startTime)
}

// Drag-to-paint: adds [startTime, endTime) as available time on `day`
// within the given date-range group (rangeKey/departmentId identify
// which range's blocks this draw belongs to -- see the grid's
// per-day range-selector pills). Any existing block in that same
// day+range group that the new range touches or overlaps is merged into
// one wider block; breaks that would now fall inside the newly-painted
// span are dropped (painting fills that gap back in), others are kept.
export function paintRange(
  blocks: ScheduleBlock[],
  day: number,
  startTime: string,
  endTime: string,
  startDate: string | null,
  endDate: string | null,
  departmentId: number | null,
): ScheduleBlock[] {
  const key = rangeKey(startDate, endDate)
  const others: ScheduleBlock[] = []
  const touched: ScheduleBlock[] = []
  for (const b of blocks) {
    if (b.day === day && rangeKey(b.startDate, b.endDate) === key && blocksOverlapOrTouch(b, startTime, endTime)) {
      touched.push(b)
    } else {
      others.push(b)
    }
  }

  const mergedStart = touched.length
    ? [startTime, ...touched.map((b) => b.startTime)].reduce((a, c) => (a < c ? a : c))
    : startTime
  const mergedEnd = touched.length
    ? [endTime, ...touched.map((b) => b.endTime)].reduce((a, c) => (a > c ? a : c))
    : endTime
  const mergedBreaks = touched
    .flatMap((b) => b.breaks)
    .filter((br) => !(toMinutesSinceMidnight(br.end) > toMinutesSinceMidnight(startTime) && toMinutesSinceMidnight(br.start) < toMinutesSinceMidnight(endTime)))
  const merged: ScheduleBlock = {
    key: touched[0]?.key ?? newBlockKey(),
    day,
    startTime: mergedStart,
    endTime: mergedEnd,
    breaks: mergedBreaks,
    departmentId: touched.find((b) => b.departmentId !== null)?.departmentId ?? departmentId,
    startDate,
    endDate,
    sourceIds: touched.flatMap((b) => b.sourceIds),
  }
  return [...others, merged]
}

// Drag-to-erase (drag again over a painted block): removes [startTime,
// endTime) from any block it overlaps in that day+range group --
// shrinking, splitting into two, or deleting the block entirely as
// needed, and clipping/dropping breaks that no longer fall inside the
// remaining piece(s).
export function eraseRange(
  blocks: ScheduleBlock[],
  day: number,
  startTime: string,
  endTime: string,
  activeRangeKey?: string,
): ScheduleBlock[] {
  const result: ScheduleBlock[] = []
  for (const b of blocks) {
    const inScope = b.day === day && (!activeRangeKey || rangeKey(b.startDate, b.endDate) === activeRangeKey)
    if (!inScope || !blocksOverlapOrTouch(b, startTime, endTime)) {
      result.push(b)
      continue
    }
    const eraseFromBefore = startTime > b.startTime
    const eraseToAfter = endTime < b.endTime
    if (eraseFromBefore) {
      const newEnd = startTime
      result.push({
        ...b,
        key: newBlockKey(),
        endTime: newEnd,
        breaks: b.breaks.filter((br) => br.end <= newEnd),
      })
    }
    if (eraseToAfter) {
      const newStart = endTime
      result.push({
        ...b,
        key: eraseFromBefore ? newBlockKey() : b.key,
        startTime: newStart,
        breaks: b.breaks.filter((br) => br.start >= newStart),
      })
    }
    // Neither piece kept (erase fully covers the block) -- dropped.
  }
  return result
}
