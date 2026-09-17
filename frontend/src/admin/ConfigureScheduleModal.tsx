import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  Buildings,
  CalendarBlank,
  CalendarCheck,
  CalendarDots,
  CaretDoubleRight,
  Check,
  Clock,
  Coffee,
  DotsThreeVertical,
  Gear,
  Info,
  ListBullets,
  Repeat,
  Trash,
  Warning,
  X,
} from '@phosphor-icons/react'
import { ApiError, createDoctorSchedule, deleteDoctorSchedule, listAdminAppointments } from '../api'
import type { AdminAppointment, Department } from '../types'
import { formatDate, formatTimeOfDay } from '../format'
import { usePreviewPopover } from '../usePreviewPopover'
import { TimeCombobox } from '../components/ui/time-combobox'
import AdminDatePicker from './AdminDatePicker'
import SlotSettingsFields from './SlotSettingsFields'
import SlotsTimeline from './SlotsTimeline'
import ScheduleConflictPanel from './ScheduleConflictPanel'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../components/ui/alert-dialog'
import {
  DAY_NAMES,
  blockSegments,
  blocksToRowPayloads,
  checkScheduleConflicts,
  countOccurrences,
  countSlots,
  dailyOverview,
  dateToDayOfWeek,
  defaultBreakFor,
  firstMatchingDate,
  formatDurationHours,
  groupIsAmbiguous,
  mergeConflictChecks,
  newBlockKey,
  newPeriodDefaults,
  shiftDateStr,
  splitGroupForEdit,
  timelineForBlocks,
  toMinutesSinceMidnight,
  validatePeriods,
  type EditScope,
  type ScheduleBlock,
  type ScheduleBreak,
} from './doctorSchedule'

type Scope = 'this_date' | 'every_weekday' | 'selected_days' | 'custom_range'
type Step = 1 | 2 | 3 | 4 | 'conflict' | 'success'

interface Period {
  key: string
  startTime: string
  endTime: string
  breaks: ScheduleBreak[]
}

function freshPeriod(afterEndTime?: string): Period {
  const { startTime, endTime } = newPeriodDefaults(afterEndTime)
  return { key: newBlockKey(), startTime, endTime, breaks: [] }
}

function periodsFromGroup(group: ScheduleBlock[]): Period[] {
  if (group.length === 0) return [freshPeriod()]
  return group.map((b) => ({ key: newBlockKey(), startTime: b.startTime, endTime: b.endTime, breaks: b.breaks }))
}

// Step 1's four scope cards -- same underlying Scope values/semantics as
// before (see splitGroupForEdit/blocksToRowPayloads), just presented as
// selectable cards instead of a plain radio list. Descriptions stay
// generic; the specific date/weekday/range is shown in the live summary
// below once enough is picked, so this list never needs the component's
// own state.
const SCOPE_OPTIONS: { id: Scope; icon: typeof CalendarBlank; title: string; description: string }[] = [
  { id: 'this_date', icon: CalendarBlank, title: 'This day only', description: 'Apply schedule to a single date' },
  { id: 'every_weekday', icon: Repeat, title: 'Repeat weekly', description: 'Apply every week on one specific weekday' },
  { id: 'selected_days', icon: CalendarCheck, title: 'Selected days', description: 'Apply to multiple weekdays in a date range' },
  { id: 'custom_range', icon: CalendarDots, title: 'Date range', description: 'Apply to a custom date range' },
]

// "Monday–Friday" for a contiguous run, "Every Wednesday" for a single
// day, or a comma list for a non-contiguous selection -- used by the
// live summary so a schedule spanning a normal work week reads as one
// range instead of five separate day names.
function scopeWeekdaySummary(days: number[]): string {
  const sorted = [...days].sort((a, b) => a - b)
  if (sorted.length === 0) return ''
  if (sorted.length === 1) return `Every ${DAY_NAMES[sorted[0]]}`
  const contiguous = sorted.every((d, i) => i === 0 || d === sorted[i - 1] + 1)
  if (contiguous) return `${DAY_NAMES[sorted[0]]}–${DAY_NAMES[sorted[sorted.length - 1]]}`
  return sorted.map((d) => DAY_NAMES[d]).join(', ')
}

function formatMonthDay(dateStr: string): { month: string; day: number; year: number } {
  const d = new Date(`${dateStr}T00:00:00`)
  return { month: d.toLocaleDateString('en-US', { month: 'short' }), day: d.getDate(), year: d.getFullYear() }
}

// "Sep 1–30, 2026" when both ends fall in the same month, otherwise
// "Sep 28 – Oct 2, 2026" / "Dec 28, 2026 – Jan 2, 2027" -- compact date
// ranges for the live summary, distinct from formatDate()'s own
// single-date convention.
function formatDateRangeCompact(startStr: string, endStr: string): string {
  const s = formatMonthDay(startStr)
  const e = formatMonthDay(endStr)
  if (s.year === e.year && s.month === e.month) return `${s.month} ${s.day}–${e.day}, ${s.year}`
  if (s.year === e.year) return `${s.month} ${s.day} – ${e.month} ${e.day}, ${s.year}`
  return `${s.month} ${s.day}, ${s.year} – ${e.month} ${e.day}, ${e.year}`
}

// Configure Schedule -- ONE popup for both creating a new schedule and
// editing/removing an existing one, opened from the Monthly Calendar
// (ScheduleGrid.tsx) either via "+ Add Schedule" (editingGroup = []) or
// by clicking a date that already has a schedule (editingGroup = that
// date's applicable blocks, from doctorSchedule.ts's editGroupForDate).
// Four gated steps:
//   1. Scope -- create: the usual this day/every weekday/selected days/
//      custom range choice. edit: "how should this change apply"
//      (this date/this and future/entire schedule), skipped entirely
//      when the existing schedule isn't ambiguous (already scoped to
//      just this one date -- nothing to ask).
//   2. Working Hours / Breaks / Department -- pre-filled from the
//      existing schedule in edit mode. Also where "Remove this
//      schedule" lives in edit mode.
//   3. Generated Slots Preview -- the live visual timeline, its own
//      step (not folded into Review) so the admin can look at nothing
//      but the preview before moving on.
//   4. Review & Save -- the "this schedule will apply to N dates"
//      headline, a full summary, and Back/Cancel/Save Schedule (one
//      primary action).
// Persists immediately (POST/DELETE against the same doctor_schedule
// endpoints the rest of the app uses) rather than staging into a
// page-wide draft -- a schedule create/edit is one complete, terminal
// transaction, not an edit to something already visible on the page.
// Reuses splitGroupForEdit for the edit-mode scope split -- no second
// scheduling engine, no new persisted concept.
export default function ConfigureScheduleModal({
  doctorId,
  departments,
  blocks,
  defaultDuration,
  bufferMinutes,
  initialDate,
  editingGroup,
  onClose,
  onSaved,
  onChangeDuration,
  onChangeBuffer,
  onDuplicate,
  onRemove,
}: {
  doctorId: number
  departments: Department[]
  // Every persisted (or draft) schedule block for this doctor -- needed
  // to check the schedule the admin is about to save against everything
  // ELSE already on the calendar (checkScheduleConflicts). Not just
  // editingGroup: that's only the one group being edited/removed.
  blocks: ScheduleBlock[]
  defaultDuration: number
  bufferMinutes: number
  initialDate: string
  editingGroup: ScheduleBlock[]
  onClose: () => void
  onSaved: () => void
  onChangeDuration: (minutes: number) => void
  onChangeBuffer: (minutes: number) => void
  onDuplicate: () => void
  onRemove: () => void
}) {
  const clickedWeekday = dateToDayOfWeek(initialDate)
  const mode: 'create' | 'edit' = editingGroup.length > 0 ? 'edit' : 'create'
  const ambiguous = mode === 'edit' && groupIsAmbiguous(editingGroup, initialDate)
  const showScopeStep = mode === 'create' || ambiguous
  const totalSteps = showScopeStep ? 4 : 3

  const [step, setStep] = useState<Step>(showScopeStep ? 1 : 2)
  const [scope, setScope] = useState<Scope | null>(null)
  const [editScope, setEditScope] = useState<EditScope | null>(ambiguous ? null : 'this_date')
  const [weekdays, setWeekdays] = useState<number[]>([clickedWeekday])
  const [rangeStart, setRangeStart] = useState(initialDate)
  const [rangeEnd, setRangeEnd] = useState('')
  const [periods, setPeriods] = useState<Period[]>(periodsFromGroup(editingGroup))
  // A pristine snapshot of edit mode's starting hours, taken once and
  // never touched again -- Step 4's "Changed" diff compares against
  // this, not against `periods` (which is the very thing being edited).
  const [originalPeriods] = useState<Period[]>(() => periodsFromGroup(editingGroup))
  const [departmentId, setDepartmentId] = useState(
    editingGroup[0]?.departmentId ? String(editingGroup[0].departmentId) : '',
  )
  const [previewDate, setPreviewDate] = useState<string | null>(mode === 'edit' ? initialDate : null)
  const [previewWeekday, setPreviewWeekday] = useState<number | null>(mode === 'edit' ? clickedWeekday : null)
  const [previewViewMode, setPreviewViewMode] = useState<'timeline' | 'list'>('timeline')
  // Dismiss on an outside click or Escape, not just by clicking the
  // trigger again -- same fix as ScheduleMonthView.tsx/MonthCalendar.tsx's
  // own Slot settings/jump popovers (usePreviewPopover). slotSettingsOpen
  // backs two different render sites (step 3's "Change slot settings" and
  // step 4's "Edit" card, only one mounted at a time), so the same
  // containerRef is attached to whichever one is actually rendered.
  const { open: slotSettingsOpen, setOpen: setSlotSettingsOpen, containerRef: slotSettingsRef } = usePreviewPopover<HTMLDivElement>()
  const { open: actionsMenuOpen, setOpen: setActionsMenuOpen, containerRef: actionsMenuRef } = usePreviewPopover<HTMLDivElement>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDiscardOpen, setConfirmDiscardOpen] = useState(false)
  const [savedSummary, setSavedSummary] = useState('')
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') requestClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [touched])

  function toggleWeekday(day: number) {
    setTouched(true)
    setWeekdays((prev) => (prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort((a, b) => a - b)))
  }

  const resolvedWeekdays = scope === 'this_date' || scope === 'every_weekday' ? [clickedWeekday] : weekdays
  const resolvedStartDate = scope === 'this_date' ? initialDate : scope === 'custom_range' ? rangeStart || null : null
  const resolvedEndDate = scope === 'this_date' ? initialDate : scope === 'custom_range' ? rangeEnd || null : null

  const scopeValid =
    scope === 'this_date' ||
    scope === 'every_weekday' ||
    (scope === 'selected_days' && weekdays.length > 0) ||
    (scope === 'custom_range' && weekdays.length > 0 && !!rangeStart && !!rangeEnd && rangeStart <= rangeEnd)

  const periodsError = validatePeriods(periods)
  const step2Valid = periods.length > 0 && !periodsError

  const occurrenceCount =
    resolvedStartDate && resolvedEndDate ? countOccurrences(resolvedWeekdays, resolvedStartDate, resolvedEndDate) : null
  const weekdayList = resolvedWeekdays.map((d) => DAY_NAMES[d]).join(', ')

  // -- Edit-mode scope description -----------------------------------------
  // The existing group's own current bounds, described in a full
  // sentence -- shown above the this-date/this-and-future/entire-schedule
  // choice so the admin knows exactly what pattern they're changing
  // before picking how far the change should reach.
  const groupStart = editingGroup[0]?.startDate ?? null
  const groupEnd = editingGroup[0]?.endDate ?? null
  const editGroupRangeText = `every ${DAY_NAMES[clickedWeekday]}${
    groupStart || groupEnd
      ? ` from ${groupStart ? formatDate(groupStart) : 'the start'} to ${groupEnd ? formatDate(groupEnd) : 'no end date'}`
      : ', with no end date'
  }`
  const editScopeLabel = (s: EditScope): string =>
    s === 'this_date'
      ? `Only ${formatDate(initialDate)}`
      : s === 'this_and_future'
        ? 'This and future occurrences'
        : 'Entire schedule'
  const resolvedEditScope: EditScope = editScope ?? 'this_date'

  // Existing appointments that fall inside whatever date range the
  // chosen edit scope will touch -- read-only, via the same admin
  // appointments listing the Appointments tab itself uses; never
  // fetched/shown for create mode (nothing pre-existing to warn
  // about). Capped 90 days out for an open-ended scope ('this_and_
  // future'/'entire' with no end date) so the check stays a single
  // bounded query rather than an unbounded one.
  const [affectedAppointments, setAffectedAppointments] = useState<AdminAppointment[]>([])
  useEffect(() => {
    if (mode !== 'edit') return
    const from = resolvedEditScope === 'entire' ? groupStart ?? initialDate : initialDate
    const to =
      resolvedEditScope === 'this_date'
        ? initialDate
        : (resolvedEditScope === 'entire' ? groupEnd : groupEnd) ?? shiftDateStr(initialDate, 90)
    listAdminAppointments({ doctor_id: doctorId, date_from: from, date_to: to })
      .then((all) => setAffectedAppointments(all.filter((a) => ['PENDING', 'CONFIRMED', 'CHECKED_IN'].includes(a.status))))
      .catch(() => setAffectedAppointments([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, resolvedEditScope, doctorId, initialDate, groupStart, groupEnd])

  // Present-tense description of what the chosen edit scope will affect,
  // shown in Step 2/3/4 so it's never implicit which dates a change to
  // an existing schedule reaches.
  const editApplyText =
    resolvedEditScope === 'entire'
      ? `This change applies to the entire schedule (${editGroupRangeText}).`
      : resolvedEditScope === 'this_and_future'
        ? `This change applies from ${formatDate(initialDate)} onward${groupEnd ? ` to ${formatDate(groupEnd)}` : ', with no end date'}.`
        : `This change applies only to ${formatDate(initialDate)}.`

  // A full sentence explaining exactly which dates a NEW schedule will
  // touch, shown in Step 1 before any hours are configured -- recurrence
  // must never be implicit, so each scope gets its own explicit wording
  // rather than one generic template. Null while the scope's own inputs
  // (weekdays, date range) aren't complete enough to say anything yet.
  const scopeExplanation: string | null =
    scope === 'this_date'
      ? `This schedule will apply only on ${new Date(`${initialDate}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}.`
      : scope === 'every_weekday'
        ? `This schedule applies every ${DAY_NAMES[clickedWeekday]}, with no end date, until you edit or remove it.`
        : scope === 'selected_days'
          ? weekdays.length > 0
            ? `This schedule applies ${scopeWeekdaySummary(weekdays)}, with no end date, until you edit or remove it.`
            : null
          : scope === 'custom_range'
            ? weekdays.length > 0 && rangeStart && rangeEnd && occurrenceCount !== null
              ? `${scopeWeekdaySummary(weekdays)} · ${formatDateRangeCompact(rangeStart, rangeEnd)} (${occurrenceCount} date${occurrenceCount === 1 ? '' : 's'})`
              : null
            : null

  // Inline feedback shown near the footer instead of a silently-disabled
  // Next -- reuses the same rules as scopeValid, just spelled out one at
  // a time so the admin knows exactly what's missing.
  const scopeValidationMessage: string | null =
    scope === 'selected_days' && weekdays.length === 0
      ? 'Select at least one working day.'
      : scope === 'custom_range'
        ? weekdays.length === 0
          ? 'Select at least one working day.'
          : !rangeStart
            ? 'Select a start date to continue.'
            : !rangeEnd
              ? 'Select an end date to continue.'
              : rangeStart > rangeEnd
                ? 'End date must be on or after the start date.'
                : null
        : null

  // Step 4's headline -- the one thing the admin must see before Save.
  const reviewHeadline =
    mode === 'edit'
      ? editApplyText
      : occurrenceCount !== null
        ? `This schedule will apply to ${occurrenceCount} date${occurrenceCount === 1 ? '' : 's'}.`
        : `This schedule will apply every ${weekdayList}, with no end date.`

  const reviewAppliesTo =
    mode === 'edit'
      ? editScopeLabel(resolvedEditScope)
      : scope === 'this_date'
        ? formatDate(initialDate)
        : occurrenceCount !== null
          ? `${weekdayList}, ${formatDate(resolvedStartDate!)} – ${formatDate(resolvedEndDate!)} (${occurrenceCount} date${occurrenceCount === 1 ? '' : 's'})`
          : `Every ${weekdayList}`

  // Success screen's own headline -- "Applied to N dates" rather than
  // reviewHeadline's future-tense "will apply", split from the detail
  // line (reviewAppliesTo, reused as-is) so the big bold number reads
  // on its own. N is never hardcoded -- occurrenceCount is the same
  // count Step 4 already showed before Save was even clicked.
  const successHeadline =
    mode === 'edit'
      ? 'Schedule updated'
      : occurrenceCount !== null
        ? `Applied to ${occurrenceCount} date${occurrenceCount === 1 ? '' : 's'}`
        : `Applied every ${weekdayList}`

  // Past-tense version shown on the success screen after Save actually
  // persists.
  function pastTenseSummary(): string {
    if (mode === 'edit') {
      return `The schedule has been updated -- ${editApplyText.charAt(0).toLowerCase()}${editApplyText.slice(1)}`
    }
    if (scope === 'this_date') return `The schedule has been applied to ${formatDate(initialDate)}.`
    if (scope === 'custom_range' && resolvedStartDate && resolvedEndDate) {
      return `The schedule has been applied from ${formatDate(resolvedStartDate)} to ${formatDate(resolvedEndDate)} (${weekdayList}).`
    }
    return `The schedule has been applied every ${weekdayList}.`
  }

  const previewBlocks: ScheduleBlock[] = periods.map((p) => ({
    key: p.key,
    day: clickedWeekday,
    startTime: p.startTime,
    endTime: p.endTime,
    breaks: p.breaks,
    departmentId: null,
    startDate: null,
    endDate: null,
    sourceIds: [],
  }))
  const previewSegments = periodsError ? [] : timelineForBlocks(previewBlocks, defaultDuration, bufferMinutes)

  // Edit mode Step 1's "Current Schedule (Preview)" -- the ORIGINAL
  // persisted schedule (editingGroup, untouched by whatever the admin
  // is about to change in Step 2), so the admin can see exactly what
  // they're editing before picking how far the change should reach.
  const currentScheduleSegments = timelineForBlocks(editingGroup, defaultDuration, bufferMinutes)
  const previewTotal = countSlots(previewSegments).total
  const currentTotal = countSlots(currentScheduleSegments).total

  // "Available time" figure -- total working minutes across every
  // period, breaks subtracted. Deliberately not derived from the
  // timeline segments (already sliced into duration+buffer chunks,
  // which would undercount a day whose last few minutes don't divide
  // evenly) -- this is the day's actual working span, not the slot
  // count times the slot length. Shared by the draft (`periods`) and,
  // in edit mode, the original (`originalPeriods`) so Step 3 can show
  // both without a second calculation.
  function workingMinutesFor(pds: Period[]): number {
    return pds.reduce((sum, p) => {
      const periodMinutes = toMinutesSinceMidnight(p.endTime) - toMinutesSinceMidnight(p.startTime)
      const breakMinutes = p.breaks.reduce(
        (s, b) => s + (toMinutesSinceMidnight(b.end) - toMinutesSinceMidnight(b.start)),
        0,
      )
      return sum + Math.max(0, periodMinutes - breakMinutes)
    }, 0)
  }
  const totalWorkingMinutes = workingMinutesFor(periods)
  const currentWorkingMinutes = workingMinutesFor(originalPeriods)
  const hasBreaks = periods.some((p) => p.breaks.length > 0)

  // Step 3/4 (edit mode)'s "what changed" diffs -- before/after per
  // field, so the admin can verify what they're about to change
  // without having to remember what Step 2 looked like before they
  // touched it.
  const oldHours = originalPeriods.map((p) => `${formatTimeOfDay(p.startTime)} – ${formatTimeOfDay(p.endTime)}`)
  const newHours = periods.map((p) => `${formatTimeOfDay(p.startTime)} – ${formatTimeOfDay(p.endTime)}`)
  const hoursChanged = JSON.stringify(oldHours) !== JSON.stringify(newHours)
  const oldBreakRanges = originalPeriods.flatMap((p) => p.breaks).map((b) => `${formatTimeOfDay(b.start)} – ${formatTimeOfDay(b.end)}`)
  const newBreakRanges = periods.flatMap((p) => p.breaks).map((b) => `${formatTimeOfDay(b.start)} – ${formatTimeOfDay(b.end)}`)
  const breaksChanged = JSON.stringify(oldBreakRanges) !== JSON.stringify(newBreakRanges)
  const originalDepartmentId = editingGroup[0]?.departmentId ? String(editingGroup[0].departmentId) : ''
  const departmentChanged = originalDepartmentId !== departmentId
  const totalSlotsChanged = currentTotal !== previewTotal

  // Edit mode's "what changed" field list -- one row per tracked
  // field (working hours, breaks, department, total slots per day),
  // shared verbatim by Step 3's Change Summary column and Step 4's
  // Changes section so there's exactly one place this comparison is
  // rendered, not two copies that could drift.
  const changeSummaryFields = (
    <>
      <div className="schedule-changesummary-field">
        <span className="field-label">Working hours</span>
        {hoursChanged ? (
          Array.from({ length: Math.max(oldHours.length, newHours.length) }).map((_, i) => (
            <div key={i} className="schedule-changed-row">
              <span className="schedule-changed-old">{oldHours[i] ?? '—'}</span>
              <span className="arrow">{'→'}</span>
              <span className="schedule-changed-new">{newHours[i] ?? '—'}</span>
            </div>
          ))
        ) : (
          <div className="schedule-changed-row">
            <span>{newHours.join(', ')}</span>
            <span className="schedule-nochange-tag">No change</span>
          </div>
        )}
      </div>

      {(oldBreakRanges.length > 0 || newBreakRanges.length > 0) && (
        <div className="schedule-changesummary-field">
          <span className="field-label">Breaks</span>
          {breaksChanged ? (
            Array.from({ length: Math.max(oldBreakRanges.length, newBreakRanges.length) }).map((_, i) => (
              <div key={i} className="schedule-changed-row">
                <span className="schedule-changed-old">{oldBreakRanges[i] ?? 'None'}</span>
                <span className="arrow">{'→'}</span>
                <span className="schedule-changed-new">{newBreakRanges[i] ?? 'None'}</span>
              </div>
            ))
          ) : (
            <div className="schedule-changed-row">
              <span>{newBreakRanges.join(', ')}</span>
              <span className="schedule-nochange-tag">No change</span>
            </div>
          )}
        </div>
      )}

      <div className="schedule-changesummary-field">
        <span className="field-label">Department</span>
        {departmentChanged ? (
          <div className="schedule-changed-row">
            <span className="schedule-changed-old">{departmentName(originalDepartmentId)}</span>
            <span className="arrow">{'→'}</span>
            <span className="schedule-changed-new">{departmentName(departmentId)}</span>
          </div>
        ) : (
          <div className="schedule-changed-row">
            <span>{departmentName(departmentId)}</span>
            <span className="schedule-nochange-tag">No change</span>
          </div>
        )}
      </div>

      <div className="schedule-changesummary-field">
        <span className="field-label">Total slots per day</span>
        {totalSlotsChanged ? (
          <div className="schedule-changed-row">
            <span className="schedule-changed-old">{currentTotal}</span>
            <span className="arrow">{'→'}</span>
            <span className="schedule-changed-new">{previewTotal}</span>
          </div>
        ) : (
          <div className="schedule-changed-row">
            <span>{previewTotal}</span>
            <span className="schedule-nochange-tag">No change</span>
          </div>
        )}
      </div>
    </>
  )

  // Step 3 is a preview, not an editor -- when it comes up empty, the
  // admin needs to know WHY without guessing, since the fix (Back to
  // Step 2) isn't obvious otherwise.
  const previewEmptyReason: string | null =
    !periodsError && periods.length > 0 && previewTotal === 0
      ? previewSegments.some((s) => s.type === 'break')
        ? 'All working periods are covered by breaks.'
        : 'Your working hours are shorter than the configured appointment duration.'
      : null

  // The weekdays this preview can be switched between -- only when the
  // scope actually spans more than one (create mode's selected_days/
  // custom_range); every other scope has exactly one weekday, so there
  // is nothing to choose and no dropdown is shown.
  const previewWeekdayOptions = mode === 'create' && resolvedWeekdays.length > 1 ? resolvedWeekdays : null

  // Schedule-overlap conflicts -- checked against everything ELSE on the
  // calendar (never against editingGroup's own rows, which are about to
  // be replaced, not duplicated). A separate concern from
  // affectedAppointments above: an appointment conflict never blocks
  // Save, an overlap conflict always does (see checkScheduleConflicts'
  // own docstring in doctorSchedule.ts).
  const candidateSegments = periodsError ? [] : periods.flatMap((p) => blockSegments(p.startTime, p.endTime, p.breaks))
  const excludeSourceIds = editingGroup.flatMap((b) => b.sourceIds)
  const conflictCheck =
    candidateSegments.length === 0
      ? { conflicts: [], conflictDates: new Set<string>(), totalDates: null }
      : mode === 'create'
        ? mergeConflictChecks(
            resolvedWeekdays.map((day) =>
              checkScheduleConflicts(blocks, excludeSourceIds, day, candidateSegments, resolvedStartDate, resolvedEndDate),
            ),
          )
        : checkScheduleConflicts(
            blocks,
            excludeSourceIds,
            clickedWeekday,
            candidateSegments,
            resolvedEditScope === 'entire' ? groupStart : initialDate,
            resolvedEditScope === 'this_date' ? initialDate : groupEnd,
          )
  const hasConflict = conflictCheck.conflicts.length > 0

  function departmentName(id: string): string {
    if (!id) return 'All departments'
    return departments.find((d) => d.id === Number(id))?.name ?? 'All departments'
  }

  function previewDateForWeekday(day: number): string {
    const anchor = resolvedStartDate ?? initialDate
    return firstMatchingDate([day], anchor, resolvedEndDate) ?? anchor
  }

  function goToStep3() {
    if (hasConflict) {
      setStep('conflict')
      return
    }
    if (mode === 'create') {
      const day = resolvedWeekdays[0] ?? clickedWeekday
      setPreviewWeekday(day)
      setPreviewDate(previewDateForWeekday(day))
    }
    setStep(3)
  }

  function handlePreviewWeekdayChange(day: number) {
    setPreviewWeekday(day)
    setPreviewDate(previewDateForWeekday(day))
  }

  function isDirty(): boolean {
    return touched
  }

  function requestClose() {
    if (step !== 'success' && isDirty()) {
      setConfirmDiscardOpen(true)
      return
    }
    onClose()
  }

  function resetForAnother() {
    setStep(showScopeStep ? 1 : 2)
    setScope(null)
    setWeekdays([clickedWeekday])
    setRangeStart(initialDate)
    setRangeEnd('')
    setPeriods([freshPeriod()])
    setDepartmentId('')
    setPreviewDate(null)
    setError(null)
    setTouched(false)
  }

  async function handleSave() {
    // Final guard, in case something changed since Step 3's own check
    // (e.g. the admin came straight back from Step 2 without revisiting
    // the preview) -- never let a save reach the backend already known
    // to overlap.
    if (hasConflict) {
      setStep('conflict')
      return
    }
    setBusy(true)
    setError(null)
    const departmentIdNum = departmentId ? Number(departmentId) : null

    if (mode === 'create') {
      const payloads = resolvedWeekdays.flatMap((day) =>
        periods.flatMap((p) =>
          blockSegments(p.startTime, p.endTime, p.breaks).map((seg) => ({
            day_of_week: day,
            start_time: seg.start,
            end_time: seg.end,
            start_date: resolvedStartDate,
            end_date: resolvedEndDate,
            department_id: departmentIdNum,
          })),
        ),
      )
      let createdCount = 0
      try {
        for (const payload of payloads) {
          await createDoctorSchedule(doctorId, payload)
          createdCount++
        }
        setSavedSummary(pastTenseSummary())
        onSaved()
        setStep('success')
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : 'Could not save this schedule'
        setError(
          createdCount > 0
            ? `${msg} -- ${createdCount} of ${payloads.length} entries were already created before this happened. Check the calendar, then adjust and retry for the remaining dates.`
            : `${msg} -- nothing was saved.`,
        )
      } finally {
        setBusy(false)
      }
      return
    }

    // Edit mode -- replace editingGroup's own persisted rows with
    // whatever splitGroupForEdit says the chosen scope should produce.
    const replacementBlocks: ScheduleBlock[] = periods.map((p) => ({
      key: newBlockKey(),
      day: clickedWeekday,
      startTime: p.startTime,
      endTime: p.endTime,
      breaks: p.breaks,
      departmentId: departmentIdNum,
      startDate: null,
      endDate: null,
      sourceIds: [],
    }))
    const finalGroup = splitGroupForEdit(editingGroup, initialDate, resolvedEditScope, replacementBlocks)
    try {
      for (const id of editingGroup.flatMap((b) => b.sourceIds)) {
        await deleteDoctorSchedule(doctorId, id)
      }
      for (const payload of blocksToRowPayloads(finalGroup)) {
        await createDoctorSchedule(doctorId, payload)
      }
      setSavedSummary(pastTenseSummary())
      onSaved()
      setStep('success')
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Could not save this change'
      setError(`${msg} -- check the calendar and retry; some of this edit may already have been applied.`)
    } finally {
      setBusy(false)
    }
  }

  const wide = step === 2 || step === 3 || step === 4 || step === 'success' || step === 'conflict' || (step === 1 && mode === 'edit')
  const extraWide = step === 3 && mode === 'edit'
  const displayStep = typeof step === 'number' ? (showScopeStep ? step : step - 1) : step === 'conflict' ? (showScopeStep ? 2 : 1) : step
  const stepLabels = showScopeStep
    ? [mode === 'edit' ? 'Update Scope' : 'Schedule Scope', 'Working Hours', 'Preview', 'Review & Save']
    : ['Working Hours', 'Preview', 'Review & Save']
  const panelClassName = [
    'modal-panel',
    'schedule-modal-panel',
    wide && 'schedule-modal-wide',
    extraWide && 'schedule-modal-wide-3col',
  ].filter(Boolean).join(' ')

  return createPortal(
    <div className="modal-overlay schedule-modal-overlay" onClick={requestClose}>
      <div
        className={panelClassName}
        role="dialog"
        aria-modal="true"
        aria-label="Configure Schedule"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={requestClose} aria-label="Close">
          <X size={20} />
        </button>

        <div className="schedule-modal-header">
          <div className="schedule-modal-header-top">
            <h3 className="appointment-details-heading" style={{ margin: 0 }}>
              {mode === 'edit' && step !== 'success' ? 'Edit Schedule' : 'Configure Schedule'}
            </h3>
            {mode === 'edit' && step !== 'success' && step !== 'conflict' && <span className="schedule-modal-badge">Existing schedule</span>}
            {mode === 'edit' && step !== 'success' && step !== 'conflict' && (
              <div className="schedule-month-jump schedule-actions-menu" ref={actionsMenuRef}>
                <button
                  type="button" className="icon-btn"
                  aria-label="Schedule actions"
                  onClick={() => setActionsMenuOpen((v) => !v)}
                >
                  <DotsThreeVertical size={18} weight="bold" />
                </button>
                {actionsMenuOpen && (
                  <div className="schedule-month-jump-popover schedule-actions-popover">
                    <button
                      type="button" className="link"
                      onClick={() => { setActionsMenuOpen(false); onDuplicate() }}
                    >
                      <CalendarDots size={16} /> Duplicate Schedule
                    </button>
                    <button
                      type="button" className="link danger"
                      onClick={() => { setActionsMenuOpen(false); onRemove() }}
                    >
                      <Trash size={16} /> Remove Schedule
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
          {step === 1 && mode === 'edit' && (
            <p className="muted schedule-modal-subtitle">
              {new Date(`${initialDate}T00:00:00`).toLocaleDateString('en-US', {
                weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
              })}
            </p>
          )}
          {step === 2 && (
            <p className="muted schedule-modal-subtitle">
              Set working hours, breaks and department for the selected days.
            </p>
          )}
          {step === 3 && (
            <p className="muted schedule-modal-subtitle">
              Preview the appointment slots that will be generated from your schedule.
            </p>
          )}
          {step === 4 && (
            <p className="muted schedule-modal-subtitle">
              {mode === 'edit' ? 'Review your changes before saving.' : 'Review your configuration before saving.'}
            </p>
          )}
          {step === 'conflict' && (
            <p className="muted schedule-modal-subtitle">
              This schedule overlaps with an existing schedule.
            </p>
          )}
          {step === 'success' && (
            <p className="muted schedule-modal-subtitle">
              Your schedule has been successfully saved.
            </p>
          )}
          <div className="schedule-modal-stepper" aria-label={step === 'success' ? 'All steps complete' : `Step ${displayStep} of ${totalSteps}`}>
            {stepLabels.map((label, i) => {
              const stepNum = i + 1
              const currentNum = typeof displayStep === 'number' ? displayStep : stepLabels.length
              const state = step === 'success' ? 'done' : stepNum < currentNum ? 'done' : stepNum === currentNum ? 'current' : 'upcoming'
              return (
                <div key={label} className={`schedule-stepper-item ${state}`}>
                  <span className="schedule-stepper-dot" aria-hidden="true">
                    {state === 'done' ? <Check size={12} weight="bold" /> : stepNum}
                  </span>
                  <span className="schedule-stepper-label">{label}</span>
                  {i < stepLabels.length - 1 && <span className="schedule-stepper-line" aria-hidden="true" />}
                </div>
              )
            })}
          </div>
        </div>

        {error && <p className="error">{error}</p>}

        {step === 1 && mode === 'create' && (
          <div className="schedule-modal-body">
            <h4 className="schedule-step1-title">When should this schedule apply?</h4>
            <p className="muted schedule-step1-subtitle">
              Choose when this schedule should be applied. You’ll set working hours, breaks and department in the
              next step.
            </p>

            <div className="schedule-scope-cards" role="radiogroup" aria-label="Schedule scope">
              {SCOPE_OPTIONS.map((opt) => {
                const OptIcon = opt.icon
                const selected = scope === opt.id
                return (
                  <button
                    key={opt.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    className={`schedule-scope-card${selected ? ' selected' : ''}`}
                    onClick={() => { setTouched(true); setScope(opt.id) }}
                  >
                    <OptIcon size={22} weight={selected ? 'fill' : 'regular'} />
                    <span className="schedule-scope-card-title">{opt.title}</span>
                    <span className="schedule-scope-card-desc">{opt.description}</span>
                  </button>
                )
              })}
            </div>

            {(scope === 'selected_days' || scope === 'custom_range') && (
              <div className="schedule-field-group">
                <span className="field-label">Which days?</span>
                <div className="day-multiselect" role="group" aria-label="Weekdays">
                  {DAY_NAMES.slice(1).map((name, i) => {
                    const day = i + 1
                    return (
                      <button
                        key={day} type="button"
                        className={weekdays.includes(day) ? 'selected' : ''}
                        aria-pressed={weekdays.includes(day)}
                        onClick={() => toggleWeekday(day)}
                      >
                        {name.slice(0, 3)}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
            {scope === 'custom_range' && (
              <div className="schedule-field-grid">
                <div className="schedule-field-group">
                  <span className="field-label">Start date</span>
                  <AdminDatePicker value={rangeStart} onChange={(v) => { setTouched(true); setRangeStart(v) }} placeholder="Select start date" />
                </div>
                <div className="schedule-field-group">
                  <span className="field-label">End date</span>
                  <AdminDatePicker value={rangeEnd} onChange={(v) => { setTouched(true); setRangeEnd(v) }} placeholder="Select end date" />
                </div>
              </div>
            )}
            {scopeExplanation && (
              <p className="schedule-scope-summary">
                <Info size={16} weight="fill" />
                <span>{scopeExplanation}</span>
              </p>
            )}
            {scope && scopeValidationMessage && <p className="schedule-scope-validation">{scopeValidationMessage}</p>}

            <div className="schedule-modal-footer">
              <button type="button" className="btn-secondary btn btn-sm" onClick={requestClose}>
                Cancel
              </button>
              <button type="button" className="btn btn-sm" disabled={!scopeValid} onClick={() => setStep(2)}>
                Next
              </button>
            </div>
          </div>
        )}

        {step === 1 && mode === 'edit' && (
          <div className="schedule-modal-body schedule-modal-review">
            <div className="schedule-review-summary">
              <span className="field-label">How should this change apply?</span>
              <div className="schedule-scope-cards edit" role="radiogroup" aria-label="Edit scope">
                {(
                  [
                    { id: 'this_date', icon: CalendarBlank, title: 'This date only', description: `Only ${formatDate(initialDate)} will be changed.` },
                    { id: 'this_and_future', icon: CaretDoubleRight, title: 'This and future occurrences', description: `This change will apply from ${formatDate(initialDate)} onward.` },
                    { id: 'entire', icon: CalendarDots, title: 'Entire schedule', description: 'This change will apply to all dates in this recurring schedule.' },
                  ] as { id: EditScope; icon: typeof CalendarBlank; title: string; description: string }[]
                ).map((opt) => {
                  const OptIcon = opt.icon
                  const selected = editScope === opt.id
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      className={`schedule-scope-card${selected ? ' selected' : ''}`}
                      onClick={() => { setTouched(true); setEditScope(opt.id) }}
                    >
                      <OptIcon size={22} weight={selected ? 'fill' : 'regular'} />
                      <span className="schedule-scope-card-title">{opt.title}</span>
                      <span className="schedule-scope-card-desc">{opt.description}</span>
                    </button>
                  )
                })}
              </div>

              <p className="schedule-scope-summary">
                <Info size={16} weight="fill" />
                <span>
                  You're editing a recurring schedule
                  <br />
                  Current schedule: {editGroupRangeText.charAt(0).toUpperCase()}{editGroupRangeText.slice(1)}
                  <br />
                  Working hours: {originalPeriods.map((p) => `${formatTimeOfDay(p.startTime)} – ${formatTimeOfDay(p.endTime)}`).join(', ')}
                </span>
              </p>

              {editScope && (
                <p className="schedule-scope-summary">
                  <Info size={16} weight="fill" />
                  <span>{editApplyText}</span>
                </p>
              )}

              {affectedAppointments.length > 0 && (
                <p className="schedule-scope-summary warning">
                  <Warning size={16} weight="fill" />
                  <span>
                    Some appointments already exist during this period. Changing this schedule will not
                    automatically move existing appointments.
                  </span>
                </p>
              )}
            </div>

            <div className="schedule-review-preview">
              <div className="schedule-preview-heading">
                <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Current Schedule (Preview)</p>
              </div>
              <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                This is the existing schedule for the selected date.
              </p>
              <SlotsTimeline segments={currentScheduleSegments} />
              <dl className="schedule-summary-list" style={{ marginTop: 'var(--space-3)' }}>
                <dt>Department</dt>
                <dd>{departmentName(originalDepartmentId)}</dd>
                <dt>Appointment duration</dt>
                <dd>{defaultDuration} minutes</dd>
                <dt>Buffer time</dt>
                <dd>{bufferMinutes === 0 ? 'No buffer' : `${bufferMinutes} minutes`}</dd>
              </dl>
              <p className="schedule-scope-summary" style={{ marginTop: 'var(--space-3)' }}>
                <Info size={16} weight="fill" />
                <span>Make changes in the next step. You'll be able to edit working hours, breaks and department.</span>
              </p>
            </div>

            <div className="schedule-modal-footer schedule-modal-footer-full">
              <button type="button" className="btn-secondary btn btn-sm" onClick={requestClose}>
                Cancel
              </button>
              <button type="button" className="btn btn-sm" disabled={!editScope} onClick={() => setStep(2)}>
                Next
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="schedule-modal-body schedule-modal-review">
          <div className="schedule-review-summary">
            <div className="schedule-field-group">
              <span className="field-label">Working days</span>
              <p className="muted schedule-sidebar-note" style={{ margin: '0 0 6px' }}>
                {mode === 'create'
                  ? 'This schedule will apply to the following days (from Step 1).'
                  : 'This schedule currently applies to:'}
              </p>
              <div className="day-multiselect readonly" role="group" aria-label="Working days for this schedule">
                {DAY_NAMES.slice(1).map((name, i) => {
                  const day = i + 1
                  const isSelected = mode === 'create' ? resolvedWeekdays.includes(day) : day === clickedWeekday
                  return (
                    <span key={day} className={isSelected ? 'selected' : ''}>
                      {name.slice(0, 3)}
                    </span>
                  )
                })}
              </div>
              {mode === 'edit' && (
                <p className="muted schedule-sidebar-note" style={{ margin: '6px 0 0' }}>
                  Scope changes -- how far this edit reaches -- are made in Step 1, not here.
                </p>
              )}
            </div>

            {mode === 'edit' && (
              <p className="schedule-scope-summary">
                <Info size={16} weight="fill" />
                <span>{editApplyText}</span>
              </p>
            )}

            <span className="field-label">Working hours</span>
            <p className="muted schedule-sidebar-note" style={{ margin: '0 0 6px' }}>
              Add one or more working periods for each selected day.
            </p>
            {periods.map((p, i) => (
              <div key={p.key} className="schedule-day-period">
                <span className="schedule-period-label">Period {i + 1}</span>
                <div className="schedule-period-row">
                  <TimeCombobox
                    value={p.startTime}
                    onChange={(v) => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, startTime: v } : x))) }}
                    durationMinutes={defaultDuration}
                    ariaLabel={`Period ${i + 1} start time`}
                  />
                  <span className="arrow">{'–'}</span>
                  <TimeCombobox
                    value={p.endTime}
                    onChange={(v) => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, endTime: v } : x))) }}
                    durationMinutes={defaultDuration}
                    ariaLabel={`Period ${i + 1} end time`}
                  />
                  {periods.length > 1 && (
                    <button
                      type="button" className="icon-btn danger"
                      aria-label={`Remove period ${i + 1}`}
                      onClick={() => { setTouched(true); setPeriods((prev) => prev.filter((x) => x.key !== p.key)) }}
                    >
                      <Trash size={16} />
                    </button>
                  )}
                </div>

                <div className="schedule-field-group schedule-breaks">
                  <span className="field-label">Breaks (optional)</span>
                  {p.breaks.map((br, bi) => (
                    <div key={bi} className="schedule-break-row">
                      <TimeCombobox
                        value={br.start}
                        onChange={(v) => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, breaks: x.breaks.map((b, xi) => (xi === bi ? { ...b, start: v } : b)) } : x))) }}
                        durationMinutes={defaultDuration}
                        ariaLabel={`Period ${i + 1} break ${bi + 1} start`}
                      />
                      <span className="arrow">{'→'}</span>
                      <TimeCombobox
                        value={br.end}
                        onChange={(v) => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, breaks: x.breaks.map((b, xi) => (xi === bi ? { ...b, end: v } : b)) } : x))) }}
                        durationMinutes={defaultDuration}
                        ariaLabel={`Period ${i + 1} break ${bi + 1} end`}
                      />
                      <button
                        type="button" className="icon-btn danger"
                        aria-label={`Remove period ${i + 1} break ${bi + 1}`}
                        onClick={() => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, breaks: x.breaks.filter((_, xi) => xi !== bi) } : x))) }}
                      >
                        <Trash size={16} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button" className="link"
                    onClick={() => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, breaks: [...x.breaks, defaultBreakFor(x.startTime, x.endTime)] } : x))) }}
                  >
                    + Add a break
                  </button>
                </div>
              </div>
            ))}
            <button
              type="button" className="link"
              onClick={() => { setTouched(true); setPeriods((prev) => [...prev, freshPeriod(prev[prev.length - 1]?.endTime)]) }}
            >
              + Add another working period
            </button>

            {periodsError && <p className="error">{periodsError}</p>}

            <label className="schedule-field-group">
              <span className="field-label">Department</span>
              <select value={departmentId} onChange={(e) => { setTouched(true); setDepartmentId(e.target.value) }}>
                <option value="">All departments</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
              <span className="muted schedule-sidebar-note">
                Leave as "All departments" to use this schedule across every department.
              </span>
            </label>
          </div>

          <div className="schedule-review-preview">
            <div className="schedule-preview-heading">
              <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>
                {mode === 'edit' ? 'Schedule Preview (Updated)' : 'Schedule summary'}
              </p>
            </div>
            <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
              {mode === 'edit'
                ? 'A visual representation of the current configuration.'
                : "Here's what you've configured so far."}
            </p>

            {mode === 'create' && (
              <dl className="schedule-summary-list">
                <dt>Working days</dt>
                <dd>{weekdayList}</dd>
                <dt>Working hours</dt>
                <dd>
                  {periods.map((p) => (
                    <div key={p.key}>{formatTimeOfDay(p.startTime)} – {formatTimeOfDay(p.endTime)}</div>
                  ))}
                </dd>
                {hasBreaks && (
                  <>
                    <dt>Breaks</dt>
                    <dd>
                      {periods.flatMap((p) => p.breaks).map((br, i) => (
                        <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                      ))}
                    </dd>
                  </>
                )}
                <dt>Department</dt>
                <dd>{departmentName(departmentId)}</dd>
                <dt>Applied dates</dt>
                <dd>
                  {occurrenceCount !== null
                    ? `${formatDate(resolvedStartDate!)} – ${formatDate(resolvedEndDate!)}`
                    : `Every ${weekdayList}`}
                </dd>
              </dl>
            )}

            {periodsError ? (
              <p className="muted schedule-sidebar-note">Fix the error above to see the preview.</p>
            ) : (
              <div className="slots-timeline">
                {dailyOverview(periods).map((seg, i) => (
                  <div key={i} className="slots-timeline-row">
                    <span className="slots-timeline-time">{formatTimeOfDay(seg.start)}</span>
                    <div className={`slots-timeline-content ${seg.type}`}>
                      <div className={`slots-timeline-chip ${seg.type}`}>
                        <span>{formatTimeOfDay(seg.start)} – {formatTimeOfDay(seg.end)}</span>
                        <span className="slots-timeline-badge">
                          {seg.type === 'slot' ? 'Working hours' : seg.type === 'break' ? '☕ Break' : 'Not available'}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {mode === 'create' && (
              <p className="muted schedule-sidebar-note" style={{ margin: '8px 0 0' }}>
                A visual guide to this day's shape -- the full generated appointment-slot preview is in the next
                step.
              </p>
            )}

            {mode === 'edit' && (
              <>
                <div className="schedule-current-before">
                  <div className="schedule-preview-heading">
                    <Clock size={16} />
                    <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Current Schedule (Before changes)</p>
                  </div>
                  <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                    Here's the existing schedule for comparison.
                  </p>
                  <dl className="schedule-summary-list">
                    <dt>Working hours</dt>
                    <dd>
                      {originalPeriods.map((p, i) => (
                        <div key={i}>{formatTimeOfDay(p.startTime)} – {formatTimeOfDay(p.endTime)}</div>
                      ))}
                    </dd>
                    {originalPeriods.some((p) => p.breaks.length > 0) && (
                      <>
                        <dt>Breaks</dt>
                        <dd>
                          {originalPeriods.flatMap((p) => p.breaks).map((br, i) => (
                            <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                          ))}
                        </dd>
                      </>
                    )}
                    <dt>Department</dt>
                    <dd>{departmentName(originalDepartmentId)}</dd>
                  </dl>
                </div>

                <p className="schedule-scope-summary warning" style={{ marginTop: 'var(--space-3)' }}>
                  <Warning size={16} weight="fill" />
                  <span>
                    You are making changes to an existing schedule. Review the preview before continuing -- your
                    changes will be applied according to the scope selected in Step 1.
                  </span>
                </p>
              </>
            )}
          </div>

          <div className="schedule-modal-footer schedule-modal-footer-full schedule-modal-footer-split">
            <div>
              {mode === 'edit' && (
                <button
                  type="button" className="btn-danger-outline btn btn-sm"
                  onClick={onRemove} disabled={busy}
                >
                  <Trash size={16} /> Remove Schedule
                </button>
              )}
            </div>
            <div className="schedule-modal-footer-actions">
              {showScopeStep ? (
                <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(1)}>
                  Back
                </button>
              ) : (
                <button type="button" className="btn-secondary btn btn-sm" onClick={requestClose}>
                  Cancel
                </button>
              )}
              <button type="button" className="btn btn-sm" disabled={!step2Valid} onClick={goToStep3}>
                Next
              </button>
            </div>
          </div>
          </div>
        )}

        {step === 3 && mode === 'create' && (
          <div className="schedule-modal-body schedule-modal-review">
            <div className="schedule-review-preview">
              <div className="schedule-preview-heading">
                <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Working-Hours Preview</p>
              </div>
              <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                This chops your working hours at the preview grid interval below -- it is not the real appointment
                length, which is set per appointment type.
              </p>

              <div className="schedule-preview-controls">
                {previewWeekdayOptions && (
                  <label className="inline-label schedule-preview-weekday">
                    Preview for
                    <select
                      value={previewWeekday ?? ''}
                      onChange={(e) => handlePreviewWeekdayChange(Number(e.target.value))}
                    >
                      {previewWeekdayOptions.map((d) => (
                        <option key={d} value={d}>{DAY_NAMES[d]}</option>
                      ))}
                    </select>
                  </label>
                )}
                <button
                  type="button" className="link schedule-preview-view-toggle"
                  onClick={() => setPreviewViewMode((v) => (v === 'timeline' ? 'list' : 'timeline'))}
                >
                  <ListBullets size={14} />
                  {previewViewMode === 'timeline' ? 'View as list' : 'View as timeline'}
                </button>
              </div>

              {periodsError ? (
                <p className="muted schedule-sidebar-note">Fix the error above to see the preview.</p>
              ) : previewViewMode === 'timeline' ? (
                <SlotsTimeline segments={previewSegments} emptyReason={previewEmptyReason} />
              ) : (
                <ul className="schedule-preview-list">
                  {previewSegments.map((seg, i) => (
                    <li key={i} className={seg.type}>
                      <span>{formatTimeOfDay(seg.start)} – {formatTimeOfDay(seg.end)}</span>
                      <span className="muted">
                        {seg.type === 'slot' ? 'Available' : seg.type === 'break' ? 'Break' : 'Not available'}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="muted schedule-sidebar-note">
                <strong>Schedule preview.</strong> Shows slots generated from these working hours and breaks. Actual
                bookable availability may differ based on appointments, time off, and booking rules.
              </p>
            </div>

            <div className="schedule-review-summary">
              <div className="schedule-preview-heading">
                <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Preview Summary</p>
              </div>
              {previewDate && (
                <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                  For {new Date(`${previewDate}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
                </p>
              )}
              <dl className="schedule-summary-list">
                <dt>Working hours</dt>
                <dd>
                  {periods.map((p) => (
                    <div key={p.key}>{formatTimeOfDay(p.startTime)} – {formatTimeOfDay(p.endTime)}</div>
                  ))}
                </dd>
                {hasBreaks && (
                  <>
                    <dt>Breaks</dt>
                    <dd>
                      {periods.flatMap((p) => p.breaks).map((br, i) => (
                        <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                      ))}
                    </dd>
                  </>
                )}
                <dt>Appointment duration</dt>
                <dd>{defaultDuration} minutes</dd>
                <dt>Buffer</dt>
                <dd>{bufferMinutes === 0 ? 'No buffer' : `${bufferMinutes} minutes`}</dd>
                <dt>Total slots</dt>
                <dd>{previewTotal} slot{previewTotal === 1 ? '' : 's'}</dd>
                <dt>Available time</dt>
                <dd>
                  {formatDurationHours(totalWorkingMinutes)}
                  {hasBreaks ? ' (excluding break)' : ''}
                </dd>
              </dl>

              <div className="schedule-month-jump" ref={slotSettingsRef}>
                <button
                  type="button" className="link"
                  onClick={() => setSlotSettingsOpen((v) => !v)}
                >
                  Change slot settings
                </button>
                {slotSettingsOpen && (
                  <div className="schedule-month-jump-popover schedule-slot-settings-popover">
                    <SlotSettingsFields
                      defaultDuration={defaultDuration}
                      bufferMinutes={bufferMinutes}
                      onChangeDuration={onChangeDuration}
                      onChangeBuffer={onChangeBuffer}
                    />
                  </div>
                )}
              </div>

              <p className="muted schedule-sidebar-note" style={{ margin: '8px 0 0' }}>
                These slots are generated from the schedule configuration you set in the previous step.
              </p>
            </div>

            <div className="schedule-modal-footer schedule-modal-footer-full">
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(2)} disabled={busy}>
                Back
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setStep(4)} disabled={busy}>
                Next
              </button>
            </div>
          </div>
        )}

        {step === 3 && mode === 'edit' && (
          <div className="schedule-modal-body">
            <h4 className="schedule-review-summary-title">Working-Hours Preview</h4>
            <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
              Compare the current schedule with your updated schedule before saving. This chops working hours at the
              preview grid interval -- it is not the real appointment length, which is set per appointment type.
            </p>
            {previewWeekdayOptions && (
              <label className="inline-label schedule-preview-weekday" style={{ marginBottom: 'var(--space-2)' }}>
                Preview for
                <select
                  value={previewWeekday ?? ''}
                  onChange={(e) => handlePreviewWeekdayChange(Number(e.target.value))}
                >
                  {previewWeekdayOptions.map((d) => (
                    <option key={d} value={d}>{DAY_NAMES[d]}</option>
                  ))}
                </select>
              </label>
            )}

            <div className="schedule-compare-grid">
              <div className="schedule-compare-column">
                <div className="schedule-preview-heading">
                  <CalendarBlank size={16} />
                  <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Current Working Hours (Before changes)</p>
                </div>
                <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                  Based on the existing saved schedule.
                </p>
                <SlotsTimeline segments={currentScheduleSegments} />
                <div className="schedule-compare-stats">
                  <div>
                    <span className="schedule-summary-card-label">Total slots</span>
                    <span className="schedule-summary-card-value">{currentTotal} slot{currentTotal === 1 ? '' : 's'}</span>
                  </div>
                  <div>
                    <span className="schedule-summary-card-label">Available time</span>
                    <span className="schedule-summary-card-value">{formatDurationHours(currentWorkingMinutes)}</span>
                  </div>
                </div>
              </div>

              <div className="schedule-compare-column updated">
                <div className="schedule-preview-heading">
                  <CalendarBlank size={16} />
                  <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Updated Working Hours (After changes)</p>
                </div>
                <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                  Based on your current changes.
                </p>
                {periodsError ? (
                  <p className="muted schedule-sidebar-note">Fix the error above to see the preview.</p>
                ) : (
                  <SlotsTimeline segments={previewSegments} emptyReason={previewEmptyReason} />
                )}
                <div className="schedule-compare-stats">
                  <div>
                    <span className="schedule-summary-card-label">Total slots</span>
                    <span className="schedule-summary-card-value">{previewTotal} slot{previewTotal === 1 ? '' : 's'}</span>
                  </div>
                  <div>
                    <span className="schedule-summary-card-label">Available time</span>
                    <span className="schedule-summary-card-value">{formatDurationHours(totalWorkingMinutes)}</span>
                  </div>
                </div>
              </div>

              <div className="schedule-compare-column">
                <div className="schedule-preview-heading">
                  <Info size={16} />
                  <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Change Summary</p>
                </div>
                <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                  Here's what's changing in this schedule.
                </p>

                {changeSummaryFields}

                <p className="schedule-scope-summary" style={{ marginTop: 'var(--space-2)' }}>
                  <Info size={16} weight="fill" />
                  <span>
                    <strong>Change applies to:</strong> {editScopeLabel(resolvedEditScope)}
                  </span>
                </p>

                {affectedAppointments.length > 0 && (
                  <p className="schedule-scope-summary warning" style={{ marginTop: 'var(--space-2)' }}>
                    <Warning size={16} weight="fill" />
                    <span>
                      Existing appointments are not automatically moved or cancelled. Some appointments already
                      exist during this period.
                    </span>
                  </p>
                )}
              </div>
            </div>

            <p className="muted schedule-sidebar-note" style={{ margin: 'var(--space-3) 0 0' }}>
              <strong>Schedule preview.</strong> Shows slots generated from these working hours and breaks. Actual
              bookable availability may differ based on appointments, time off, and booking rules.
            </p>

            <div className="schedule-modal-footer schedule-modal-footer-split">
              <div>
                <button
                  type="button" className="btn-danger-outline btn btn-sm"
                  onClick={onRemove} disabled={busy}
                >
                  <Trash size={16} /> Remove Schedule
                </button>
              </div>
              <div className="schedule-modal-footer-actions">
                <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(2)} disabled={busy}>
                  Back
                </button>
                <button type="button" className="btn-secondary btn btn-sm" onClick={requestClose} disabled={busy}>
                  Cancel
                </button>
                <button type="button" className="btn btn-sm" onClick={() => setStep(4)} disabled={busy}>
                  Next
                </button>
              </div>
            </div>
          </div>
        )}

        {step === 4 && mode === 'create' && (
          <div className="schedule-modal-body schedule-modal-review">
            <div className="schedule-review-summary">
              <h4 className="schedule-review-summary-title">Schedule Summary</h4>
              <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                Here's a summary of the schedule you're about to apply.
              </p>

              <div className="schedule-summary-cards">
                {showScopeStep && (
                  <div className="schedule-summary-card">
                    <CalendarBlank size={18} />
                    <div className="schedule-summary-card-body">
                      <span className="schedule-summary-card-label">Applies to</span>
                      <span className="schedule-summary-card-value">{reviewAppliesTo}</span>
                    </div>
                    <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(1)} disabled={busy}>
                      Edit
                    </button>
                  </div>
                )}

                <div className="schedule-summary-card">
                  <Clock size={18} />
                  <div className="schedule-summary-card-body">
                    <span className="schedule-summary-card-label">Working hours</span>
                    <span className="schedule-summary-card-value">
                      {periods.map((p) => (
                        <div key={p.key}>{formatTimeOfDay(p.startTime)} – {formatTimeOfDay(p.endTime)}</div>
                      ))}
                    </span>
                  </div>
                  <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(2)} disabled={busy}>
                    Edit
                  </button>
                </div>

                {hasBreaks && (
                  <div className="schedule-summary-card">
                    <Coffee size={18} />
                    <div className="schedule-summary-card-body">
                      <span className="schedule-summary-card-label">Breaks</span>
                      <span className="schedule-summary-card-value">
                        {periods.flatMap((p) => p.breaks).map((br, i) => (
                          <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                        ))}
                      </span>
                    </div>
                    <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(2)} disabled={busy}>
                      Edit
                    </button>
                  </div>
                )}

                <div className="schedule-summary-card">
                  <Buildings size={18} />
                  <div className="schedule-summary-card-body">
                    <span className="schedule-summary-card-label">Department</span>
                    <span className="schedule-summary-card-value">{departmentName(departmentId)}</span>
                  </div>
                  <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(2)} disabled={busy}>
                    Edit
                  </button>
                </div>

                <div className="schedule-summary-card">
                  <Gear size={18} />
                  <div className="schedule-summary-card-body">
                    <span className="schedule-summary-card-label">Slot settings</span>
                    <span className="schedule-summary-card-value">
                      <div>Preview grid: {defaultDuration} minutes</div>
                      <div>Buffer: {bufferMinutes === 0 ? 'No buffer' : `${bufferMinutes} minutes`}</div>
                    </span>
                  </div>
                  <div className="schedule-month-jump" ref={slotSettingsRef}>
                    <button
                      type="button" className="btn-secondary btn btn-sm"
                      onClick={() => setSlotSettingsOpen((v) => !v)} disabled={busy}
                    >
                      Edit
                    </button>
                    {slotSettingsOpen && (
                      <div className="schedule-month-jump-popover schedule-slot-settings-popover">
                        <SlotSettingsFields
                          defaultDuration={defaultDuration}
                          bufferMinutes={bufferMinutes}
                          onChangeDuration={onChangeDuration}
                          onChangeBuffer={onChangeBuffer}
                        />
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <p className="schedule-scope-summary schedule-review-headline">
                <Info size={16} weight="fill" />
                <span>{reviewHeadline}</span>
              </p>
            </div>

            <div className="schedule-review-preview">
              <div className="schedule-preview-heading">
                <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Preview</p>
              </div>
              <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                A quick preview of how a typical day will look.
              </p>
              {previewWeekdayOptions && (
                <label className="inline-label schedule-preview-weekday">
                  Preview for
                  <select
                    value={previewWeekday ?? ''}
                    onChange={(e) => handlePreviewWeekdayChange(Number(e.target.value))}
                  >
                    {previewWeekdayOptions.map((d) => (
                      <option key={d} value={d}>{DAY_NAMES[d]}</option>
                    ))}
                  </select>
                </label>
              )}
              {periodsError ? (
                <p className="muted schedule-sidebar-note">Fix the error above to see the preview.</p>
              ) : (
                <SlotsTimeline segments={previewSegments} emptyReason={previewEmptyReason} />
              )}
            </div>

            <div className="schedule-modal-footer schedule-modal-footer-full">
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(3)} disabled={busy}>
                Back
              </button>
              <button type="button" className="btn-secondary btn btn-sm" onClick={requestClose} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-sm" onClick={handleSave} disabled={busy}>
                {busy ? 'Saving…' : 'Save Schedule'}
              </button>
            </div>
          </div>
        )}

        {step === 4 && mode === 'edit' && (
          <div className="schedule-modal-body">
            <div className="schedule-scope-banner">
              <CalendarBlank size={20} />
              <div className="schedule-scope-banner-body">
                <span className="schedule-scope-banner-label">Applies to: {editScopeLabel(resolvedEditScope)}</span>
                <span className="muted schedule-sidebar-note">{editApplyText}</span>
              </div>
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(1)} disabled={busy}>
                Edit
              </button>
            </div>

            <div className="schedule-compare-grid two-col">
              <div className="schedule-compare-column">
                <div className="schedule-preview-heading">
                  <CalendarBlank size={16} />
                  <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Current Schedule (Before changes)</p>
                </div>
                <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                  This is your existing schedule.
                </p>
                <dl className="schedule-summary-list">
                  <dt>Working hours</dt>
                  <dd>
                    {originalPeriods.map((p, i) => (
                      <div key={i}>{formatTimeOfDay(p.startTime)} – {formatTimeOfDay(p.endTime)}</div>
                    ))}
                  </dd>
                  {oldBreakRanges.length > 0 && (
                    <>
                      <dt>Break</dt>
                      <dd>{oldBreakRanges.map((r, i) => <div key={i}>{r}</div>)}</dd>
                    </>
                  )}
                  <dt>Department</dt>
                  <dd>{departmentName(originalDepartmentId)}</dd>
                  <dt>Generated slots</dt>
                  <dd>{currentTotal} slots per day</dd>
                </dl>
              </div>

              <div className="schedule-compare-column updated">
                <div className="schedule-preview-heading">
                  <CalendarBlank size={16} />
                  <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Updated Schedule (After changes)</p>
                </div>
                <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                  This is how your schedule will look after saving.
                </p>
                <dl className="schedule-summary-list">
                  <dt>Working hours</dt>
                  <dd>
                    {periods.map((p) => (
                      <div key={p.key}>{formatTimeOfDay(p.startTime)} – {formatTimeOfDay(p.endTime)}</div>
                    ))}
                  </dd>
                  {newBreakRanges.length > 0 && (
                    <>
                      <dt>Break</dt>
                      <dd>{newBreakRanges.map((r, i) => <div key={i}>{r}</div>)}</dd>
                    </>
                  )}
                  <dt>Department</dt>
                  <dd>{departmentName(departmentId)}</dd>
                  <dt>Generated slots</dt>
                  <dd>{previewTotal} slots per day</dd>
                </dl>
              </div>
            </div>

            <div className="schedule-preview-heading" style={{ marginTop: 'var(--space-3)' }}>
              <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Updated schedule preview</p>
            </div>
            {periodsError ? (
              <p className="muted schedule-sidebar-note">Fix the error above to see the preview.</p>
            ) : (
              <SlotsTimeline segments={previewSegments} emptyReason={previewEmptyReason} />
            )}

            <div className="schedule-changesummary-block">
              <span className="field-label">Changes</span>
              {changeSummaryFields}
            </div>

            {affectedAppointments.length > 0 && (
              <p className="schedule-scope-summary warning">
                <Warning size={16} weight="fill" />
                <span>
                  There are {affectedAppointments.length} appointment{affectedAppointments.length === 1 ? '' : 's'} on
                  this date. Changing the schedule will not automatically move or cancel existing appointments.
                </span>
              </p>
            )}

            <p className="muted schedule-sidebar-note">
              <strong>Schedule preview.</strong> Shows slots generated from these working hours and breaks. Actual
              bookable availability may differ based on appointments, time off, and booking rules.
            </p>

            <div className="schedule-modal-footer schedule-modal-footer-split">
              <div>
                <button
                  type="button" className="btn-danger-outline btn btn-sm"
                  onClick={onRemove} disabled={busy}
                >
                  <Trash size={16} /> Remove Schedule
                </button>
              </div>
              <div className="schedule-modal-footer-actions">
                <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(3)} disabled={busy}>
                  Back
                </button>
                <button type="button" className="btn-secondary btn btn-sm" onClick={requestClose} disabled={busy}>
                  Cancel
                </button>
                <button type="button" className="btn btn-sm" onClick={handleSave} disabled={busy}>
                  {busy ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </div>
          </div>
        )}

        {step === 'conflict' && (
          <ScheduleConflictPanel
            conflicts={conflictCheck.conflicts}
            totalDates={conflictCheck.totalDates}
            conflictDateCount={conflictCheck.conflictDates.size}
            appointmentCount={mode === 'edit' ? affectedAppointments.length : 0}
            newSummary={{
              icon: CalendarBlank,
              label: 'Selected Schedule (New)',
              lines: [
                `Working hours: ${periods.map((p) => `${formatTimeOfDay(p.startTime)} – ${formatTimeOfDay(p.endTime)}`).join(', ')}`,
                `Department: ${departmentName(departmentId)}`,
                `Applies to: ${
                  mode === 'edit'
                    ? editApplyText
                    : occurrenceCount !== null
                      ? `${weekdayList}, ${formatDate(resolvedStartDate!)} – ${formatDate(resolvedEndDate!)}`
                      : `Every ${weekdayList}`
                }`,
              ],
            }}
            footer={
              <div className="schedule-modal-footer schedule-modal-footer-full">
                {mode === 'create' && showScopeStep && (
                  <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(1)}>
                    Change Dates
                  </button>
                )}
                <button type="button" className="btn-secondary btn btn-sm" onClick={requestClose}>
                  Cancel
                </button>
                <button type="button" className="btn btn-sm" onClick={() => setStep(2)}>
                  Back to {mode === 'edit' ? 'Edit' : 'Schedule'}
                </button>
              </div>
            }
          />
        )}

        {step === 'success' && (
          <div className="schedule-modal-body schedule-modal-success">
            <div className="schedule-success-check" aria-hidden="true">{'✓'}</div>
            <h3 style={{ margin: '0 0 4px' }}>
              {mode === 'edit' ? 'Schedule updated successfully!' : 'Schedule saved successfully!'}
            </h3>
            <p className="muted" style={{ margin: 0 }}>
              {mode === 'edit' ? "Your doctor's schedule has been updated." : "Your doctor's schedule has been applied."}
            </p>

            <div className="schedule-summary-card success">
              <CalendarBlank size={18} />
              <div className="schedule-summary-card-body">
                <span className="schedule-summary-card-value schedule-success-headline">{successHeadline}</span>
                <span className="schedule-summary-card-label">{reviewAppliesTo}</span>
              </div>
            </div>

            <dl className="schedule-summary-list schedule-success-details">
              <dt>Working hours</dt>
              <dd>
                {periods.map((p) => (
                  <div key={p.key}>{formatTimeOfDay(p.startTime)} – {formatTimeOfDay(p.endTime)}</div>
                ))}
              </dd>
              <dt>Department</dt>
              <dd>{departmentName(departmentId)}</dd>
              <dt>Generated slots</dt>
              <dd>{previewTotal} slot{previewTotal === 1 ? '' : 's'} per day</dd>
            </dl>

            <p className="muted" style={{ margin: 0 }}>
              {mode === 'create'
                ? 'The schedule is now active and available for appointment booking.'
                : savedSummary}
            </p>

            <div className="schedule-modal-footer">
              <button type="button" className="btn btn-sm" onClick={onClose}>
                View Calendar
              </button>
              {mode === 'create' && (
                <button type="button" className="btn-secondary btn btn-sm" onClick={resetForAnother}>
                  + Add Another Schedule
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <AlertDialog open={confirmDiscardOpen} onOpenChange={(open) => !open && setConfirmDiscardOpen(false)}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard {mode === 'edit' ? 'these changes' : 'this schedule'}?</AlertDialogTitle>
            <AlertDialogDescription>
              What you’ve entered hasn’t been saved yet. Closing now discards it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={onClose}>
              Discard
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>,
    document.body,
  )
}
