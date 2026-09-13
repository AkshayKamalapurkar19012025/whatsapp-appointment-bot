import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Info, X } from '@phosphor-icons/react'
import { ApiError, createDoctorSchedule } from '../api'
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
  countOccurrences,
  dateToDayOfWeek,
  defaultBreakFor,
  firstMatchingDate,
  newBlockKey,
  newPeriodDefaults,
  timelineForBlocks,
  validateBlockBreaks,
  type ScheduleBlock,
  type ScheduleBreak,
} from './doctorSchedule'

type Scope = 'this_date' | 'every_weekday' | 'selected_days' | 'custom_range'
type Step = 1 | 2 | 3 | 'success'

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

// Configure Schedule -- a proper modal with three gated internal steps
// (Scope -> Hours/Breaks/Department -> Preview & Save) plus a success
// screen, replacing the day panel's old inline Add Schedule wizard.
// Reuses the exact same pure helpers (doctorSchedule.ts) and the exact
// same POST /doctors/{id}/schedule endpoint the rest of the Schedule tab
// already uses -- no new backend concept, no schema change. Unlike the
// rest of the calendar (which stages edits into a page-wide draft saved
// by one bottom Save bar), "Save Schedule" here persists immediately,
// matching how AddDoctorModal/AddDepartmentModal already behave for
// creating a new record -- a new schedule is one complete, terminal
// transaction, not an edit to something already on the page.
export default function ConfigureScheduleModal({
  doctorId,
  departments,
  defaultDuration,
  bufferMinutes,
  initialDate,
  onClose,
  onSaved,
}: {
  doctorId: number
  departments: Department[]
  defaultDuration: number
  bufferMinutes: number
  initialDate: string
  onClose: () => void
  onSaved: () => void
}) {
  const clickedWeekday = dateToDayOfWeek(initialDate)

  const [step, setStep] = useState<Step>(1)
  const [scope, setScope] = useState<Scope | null>(null)
  const [weekdays, setWeekdays] = useState<number[]>([clickedWeekday])
  const [rangeStart, setRangeStart] = useState(initialDate)
  const [rangeEnd, setRangeEnd] = useState('')
  const [periods, setPeriods] = useState<Period[]>([freshPeriod()])
  const [departmentId, setDepartmentId] = useState('')
  const [previewDate, setPreviewDate] = useState<string | null>(null)
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

  const periodsError = periods.reduce<string | null>((err, p) => {
    if (err) return err
    if (!(p.startTime < p.endTime)) return 'Each working period’s end time must be after its start time'
    return validateBlockBreaks(p.startTime, p.endTime, p.breaks)
  }, null)
  const step2Valid = periods.length > 0 && !periodsError

  const occurrenceCount =
    resolvedStartDate && resolvedEndDate ? countOccurrences(resolvedWeekdays, resolvedStartDate, resolvedEndDate) : null
  const weekdayList = resolvedWeekdays.map((d) => DAY_NAMES[d]).join(', ')
  // Short form used in the Step 3 summary and the success screen, where
  // space is tight and the scope was already confirmed back in Step 1.
  const scopeSummary =
    scope === 'this_date'
      ? formatDate(initialDate)
      : occurrenceCount !== null
        ? `${weekdayList}, ${formatDate(resolvedStartDate!)} – ${formatDate(resolvedEndDate!)} (${occurrenceCount} date${occurrenceCount === 1 ? '' : 's'})`
        : `Every ${weekdayList}`

  // A full sentence explaining exactly which dates this schedule will
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

  // Step 3's headline -- the one thing the admin must see before Save:
  // exactly how many dates this touches, or (for an open-ended scope
  // with no numeric count) exactly which weekdays it recurs on.
  const reviewHeadline =
    occurrenceCount !== null
      ? `This schedule will apply to ${occurrenceCount} date${occurrenceCount === 1 ? '' : 's'}.`
      : `This schedule will apply every ${weekdayList}, with no end date.`

  // Past-tense version shown on the success screen after Save actually
  // persists -- same facts as reviewHeadline/scopeExplanation, phrased
  // as something that already happened.
  function pastTenseSummary(): string {
    if (scope === 'this_date') return `The schedule has been applied to ${formatDate(initialDate)}.`
    if (scope === 'custom_range' && resolvedStartDate && resolvedEndDate) {
      return `The schedule has been applied from ${formatDate(resolvedStartDate)} to ${formatDate(resolvedEndDate)} (${weekdayList}).`
    }
    return `The schedule has been applied every ${weekdayList}.`
  }

  const previewBlocks: ScheduleBlock[] = periods.map((p) => ({
    key: p.key,
    day: resolvedWeekdays[0] ?? clickedWeekday,
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
    const anchor = resolvedStartDate ?? initialDate
    setPreviewDate(firstMatchingDate(resolvedWeekdays, anchor, resolvedEndDate) ?? anchor)
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
    setStep(1)
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
  }

  const wide = step === 2 || step === 3 || step === 'success'

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
            <span className="schedule-modal-step">Step {step} of 3</span>
          </div>
        )}

        {error && <p className="error">{error}</p>}

        {step === 1 && (
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

        {step === 2 && (
          <div className="schedule-modal-body schedule-modal-review">
          <div className="schedule-review-summary">
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
            <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(1)}>
              Back
            </button>
            <button type="button" className="btn btn-sm" disabled={!step2Valid} onClick={goToStep3}>
              Next
            </button>
          </div>
          </div>
        )}

        {step === 3 && (
          <div className="schedule-modal-body schedule-modal-review">
            <p className="schedule-scope-summary schedule-review-headline">
              <Info size={16} weight="fill" />
              <span>{reviewHeadline}</span>
            </p>
            <div className="schedule-review-summary">
              <h4 style={{ margin: '0 0 8px' }}>Summary</h4>
              <dl className="schedule-summary-list">
                <dt>Applies to</dt>
                <dd>{scopeSummary}</dd>
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
            <div className="schedule-review-preview">
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
              <button type="button" className="btn btn-sm" onClick={resetForAnother}>
                Add Another Schedule
              </button>
            </div>
          </div>
        )}
      </div>

      <AlertDialog open={confirmDiscardOpen} onOpenChange={(open) => !open && setConfirmDiscardOpen(false)}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard this schedule?</AlertDialogTitle>
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
