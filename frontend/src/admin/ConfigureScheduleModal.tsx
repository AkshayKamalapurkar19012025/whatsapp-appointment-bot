import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Info, X } from '@phosphor-icons/react'
import { ApiError, createDoctorSchedule, deleteDoctorSchedule } from '../api'
import type { Department } from '../types'
import { formatDate, formatTimeOfDay } from '../format'
import { TimeCombobox } from '../components/ui/time-combobox'
import AdminDatePicker from './AdminDatePicker'
import SlotsTimeline from './SlotsTimeline'
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
  countOccurrences,
  dateToDayOfWeek,
  defaultBreakFor,
  firstMatchingDate,
  groupIsAmbiguous,
  newBlockKey,
  newPeriodDefaults,
  splitGroupForEdit,
  timelineForBlocks,
  validateBlockBreaks,
  type EditScope,
  type ScheduleBlock,
  type ScheduleBreak,
} from './doctorSchedule'

type Scope = 'this_date' | 'every_weekday' | 'selected_days' | 'custom_range'
type Step = 1 | 2 | 3 | 4 | 'success'

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
  defaultDuration,
  bufferMinutes,
  initialDate,
  editingGroup,
  onClose,
  onSaved,
}: {
  doctorId: number
  departments: Department[]
  defaultDuration: number
  bufferMinutes: number
  initialDate: string
  editingGroup: ScheduleBlock[]
  onClose: () => void
  onSaved: () => void
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
  const [departmentId, setDepartmentId] = useState(
    editingGroup[0]?.departmentId ? String(editingGroup[0].departmentId) : '',
  )
  const [previewDate, setPreviewDate] = useState<string | null>(mode === 'edit' ? initialDate : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDiscardOpen, setConfirmDiscardOpen] = useState(false)
  const [confirmRemoveOpen, setConfirmRemoveOpen] = useState(false)
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

  const periodsError = periods.reduce<string | null>((err, p) => {
    if (err) return err
    if (!(p.startTime < p.endTime)) return 'Each working period’s end time must be after its start time'
    return validateBlockBreaks(p.startTime, p.endTime, p.breaks)
  }, null)
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
      ? `This schedule applies only to ${formatDate(initialDate)}.`
      : scope === 'every_weekday'
        ? `This schedule applies every ${DAY_NAMES[clickedWeekday]}, with no end date, until you edit or remove it.`
        : scope === 'selected_days'
          ? weekdays.length > 0
            ? `This schedule applies every ${weekdayList}, with no end date, until you edit or remove it.`
            : null
          : scope === 'custom_range'
            ? weekdays.length > 0 && rangeStart && rangeEnd && occurrenceCount !== null
              ? `This schedule will apply to ${weekdayList} from ${formatDate(rangeStart)} to ${formatDate(rangeEnd)} (${occurrenceCount} date${occurrenceCount === 1 ? '' : 's'}).`
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

  function departmentName(id: string): string {
    if (!id) return 'All departments'
    return departments.find((d) => d.id === Number(id))?.name ?? 'All departments'
  }

  function goToStep3() {
    if (mode === 'create') {
      const anchor = resolvedStartDate ?? initialDate
      setPreviewDate(firstMatchingDate(resolvedWeekdays, anchor, resolvedEndDate) ?? anchor)
    }
    setStep(3)
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

  async function handleRemove() {
    setBusy(true)
    setError(null)
    const finalGroup = splitGroupForEdit(editingGroup, initialDate, resolvedEditScope, null)
    try {
      for (const id of editingGroup.flatMap((b) => b.sourceIds)) {
        await deleteDoctorSchedule(doctorId, id)
      }
      for (const payload of blocksToRowPayloads(finalGroup)) {
        await createDoctorSchedule(doctorId, payload)
      }
      setSavedSummary(
        resolvedEditScope === 'entire'
          ? 'The schedule has been removed entirely.'
          : resolvedEditScope === 'this_and_future'
            ? `The schedule has been removed from ${formatDate(initialDate)} onward.`
            : `The schedule has been removed for ${formatDate(initialDate)} only.`,
      )
      onSaved()
      setStep('success')
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Could not remove this schedule'
      setError(`${msg} -- check the calendar and retry.`)
    } finally {
      setBusy(false)
      setConfirmRemoveOpen(false)
    }
  }

  const wide = step === 2 || step === 3 || step === 4 || step === 'success'
  const displayStep = typeof step === 'number' ? (showScopeStep ? step : step - 1) : step

  return createPortal(
    <div className="modal-overlay schedule-modal-overlay" onClick={requestClose}>
      <div
        className={wide ? 'modal-panel schedule-modal-panel schedule-modal-wide' : 'modal-panel schedule-modal-panel'}
        role="dialog"
        aria-modal="true"
        aria-label="Configure Schedule"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={requestClose} aria-label="Close">
          <X size={20} />
        </button>

        {step !== 'success' && (
          <div className="schedule-modal-header">
            <h3 className="appointment-details-heading" style={{ margin: 0 }}>
              Configure Schedule
            </h3>
            <span className="schedule-modal-step">
              Step {displayStep} of {totalSteps}
            </span>
          </div>
        )}

        {error && <p className="error">{error}</p>}

        {step === 1 && mode === 'create' && (
          <div className="schedule-modal-body">
            <p className="muted" style={{ margin: '0 0 4px' }}>
              {new Date(`${initialDate}T00:00:00`).toLocaleDateString('en-US', {
                weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
              })}
            </p>
            <span className="field-label">How should this schedule apply?</span>
            <div className="schedule-scope-choices" role="radiogroup" aria-label="Schedule scope">
              <label className="schedule-scope-option">
                <input
                  type="radio" name="schedule-scope" checked={scope === 'this_date'}
                  onChange={() => { setTouched(true); setScope('this_date') }}
                />
                This day only ({formatDate(initialDate)})
              </label>
              <label className="schedule-scope-option">
                <input
                  type="radio" name="schedule-scope" checked={scope === 'every_weekday'}
                  onChange={() => { setTouched(true); setScope('every_weekday') }}
                />
                Every {DAY_NAMES[clickedWeekday]} (recurring)
              </label>
              <label className="schedule-scope-option">
                <input
                  type="radio" name="schedule-scope" checked={scope === 'selected_days'}
                  onChange={() => { setTouched(true); setScope('selected_days') }}
                />
                Selected days
              </label>
              <label className="schedule-scope-option">
                <input
                  type="radio" name="schedule-scope" checked={scope === 'custom_range'}
                  onChange={() => { setTouched(true); setScope('custom_range') }}
                />
                Custom date range
              </label>
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
                  <AdminDatePicker value={rangeStart} onChange={(v) => { setTouched(true); setRangeStart(v) }} label="Pick start date" />
                </div>
                <div className="schedule-field-group">
                  <span className="field-label">End date</span>
                  <AdminDatePicker value={rangeEnd} onChange={(v) => { setTouched(true); setRangeEnd(v) }} label="Pick end date" />
                </div>
              </div>
            )}
            {scopeExplanation && (
              <p className="schedule-scope-summary">
                <Info size={16} weight="fill" />
                <span>{scopeExplanation}</span>
              </p>
            )}

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
          <div className="schedule-modal-body">
            <p className="muted" style={{ margin: '0 0 4px' }}>
              {new Date(`${initialDate}T00:00:00`).toLocaleDateString('en-US', {
                weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
              })}
            </p>
            <span className="field-label">How should this change apply?</span>
            <p className="muted schedule-sidebar-note" style={{ margin: '4px 0 8px' }}>
              This schedule currently applies to {editGroupRangeText}.
            </p>
            <div className="schedule-scope-choices" role="radiogroup" aria-label="Edit scope">
              {(['this_date', 'this_and_future', 'entire'] as EditScope[]).map((s) => (
                <label key={s} className="schedule-scope-option">
                  <input
                    type="radio" name="edit-scope" checked={editScope === s}
                    onChange={() => { setTouched(true); setEditScope(s) }}
                  />
                  {editScopeLabel(s)}
                </label>
              ))}
            </div>

            <div className="schedule-modal-footer">
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
            {mode === 'create' ? (
              <div className="schedule-field-group">
                <span className="field-label">Working days</span>
                <div className="day-multiselect readonly" role="group" aria-label="Working days for this schedule">
                  {DAY_NAMES.slice(1).map((name, i) => {
                    const day = i + 1
                    return (
                      <span key={day} className={resolvedWeekdays.includes(day) ? 'selected' : ''}>
                        {name.slice(0, 3)}
                      </span>
                    )
                  })}
                </div>
              </div>
            ) : (
              <p className="schedule-scope-summary">
                <Info size={16} weight="fill" />
                <span>{editApplyText}</span>
              </p>
            )}

            <span className="field-label">Working hours</span>
            {periods.map((p, i) => (
              <div key={p.key} className="schedule-day-period">
                <div className="schedule-field-grid">
                  <label className="schedule-field-group">
                    <span className="field-label">Period {i + 1} start</span>
                    <TimeCombobox
                      value={p.startTime}
                      onChange={(v) => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, startTime: v } : x))) }}
                      durationMinutes={defaultDuration}
                      ariaLabel={`Period ${i + 1} start time`}
                    />
                  </label>
                  <label className="schedule-field-group">
                    <span className="field-label">Period {i + 1} end</span>
                    <TimeCombobox
                      value={p.endTime}
                      onChange={(v) => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, endTime: v } : x))) }}
                      durationMinutes={defaultDuration}
                      ariaLabel={`Period ${i + 1} end time`}
                    />
                  </label>
                  {periods.length > 1 && (
                    <button
                      type="button" className="link danger"
                      onClick={() => { setTouched(true); setPeriods((prev) => prev.filter((x) => x.key !== p.key)) }}
                    >
                      Remove period
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
                        type="button" className="link danger"
                        onClick={() => { setTouched(true); setPeriods((prev) => prev.map((x) => (x.key === p.key ? { ...x, breaks: x.breaks.filter((_, xi) => xi !== bi) } : x))) }}
                      >
                        Remove
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
                Optional -- leave as "All departments" if this schedule isn’t department-specific.
              </span>
            </label>

            {mode === 'edit' && (
              <button type="button" className="link danger" onClick={() => setConfirmRemoveOpen(true)} disabled={busy}>
                Remove this schedule
              </button>
            )}
          </div>

          <div className="schedule-review-preview">
            <div className="schedule-preview-heading">
              <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Generated Slots Preview</p>
            </div>
            <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
              These are the appointment slots this schedule would create -- updates live as you change hours, breaks
              or working days.
            </p>
            {periodsError ? (
              <p className="muted schedule-sidebar-note">Fix the error above to see the preview.</p>
            ) : (
              <SlotsTimeline segments={previewSegments} />
            )}
            <p className="muted schedule-sidebar-note">
              A draft preview, generated from working hours, breaks and the default appointment duration -- existing
              appointments and time off are not excluded here.
            </p>
          </div>

          <div className="schedule-modal-footer schedule-modal-footer-full">
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
        )}

        {step === 3 && (
          <div className="schedule-modal-body schedule-modal-review">
            <div className="schedule-review-preview schedule-review-preview-full">
              <div className="schedule-preview-heading">
                <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Generated Slots Preview</p>
              </div>
              {previewDate && (
                <p className="muted schedule-sidebar-note">
                  Preview for {new Date(`${previewDate}T00:00:00`).toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short' })}
                </p>
              )}
              {periodsError ? (
                <p className="muted schedule-sidebar-note">Fix the error above to see the preview.</p>
              ) : (
                <SlotsTimeline segments={previewSegments} />
              )}
              <p className="muted schedule-sidebar-note">
                A draft preview, generated from working hours, breaks and the default appointment duration --
                existing appointments and time off are not excluded here.
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

        {step === 4 && (
          <div className="schedule-modal-body schedule-modal-review">
            <p className="schedule-scope-summary schedule-review-headline">
              <Info size={16} weight="fill" />
              <span>{reviewHeadline}</span>
            </p>
            <div className="schedule-review-summary">
              <h4 style={{ margin: '0 0 8px' }}>Summary</h4>
              <dl className="schedule-summary-list">
                <dt>Applies to</dt>
                <dd>{reviewAppliesTo}</dd>
                <dt>Working hours</dt>
                <dd>
                  {periods.map((p) => (
                    <div key={p.key}>{formatTimeOfDay(p.startTime)} – {formatTimeOfDay(p.endTime)}</div>
                  ))}
                </dd>
                {periods.some((p) => p.breaks.length > 0) && (
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
              </dl>
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

        {step === 'success' && (
          <div className="schedule-modal-body schedule-modal-success">
            <div className="schedule-success-check" aria-hidden="true">{'✓'}</div>
            <h3 style={{ margin: '0 0 4px' }}>Schedule saved successfully!</h3>
            <p className="muted" style={{ margin: 0 }}>
              {savedSummary}
            </p>
            <div className="schedule-modal-footer">
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
                View Calendar
              </button>
              {mode === 'create' && (
                <button type="button" className="btn btn-sm" onClick={resetForAnother}>
                  Add Another Schedule
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

      <AlertDialog open={confirmRemoveOpen} onOpenChange={(open) => !open && setConfirmRemoveOpen(false)}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this schedule?</AlertDialogTitle>
            <AlertDialogDescription>
              {editApplyText} This can’t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={handleRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>,
    document.body,
  )
}
