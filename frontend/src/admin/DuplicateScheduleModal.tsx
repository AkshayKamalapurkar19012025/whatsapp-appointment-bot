import { useState } from 'react'
import { createPortal } from 'react-dom'
import { Buildings, CalendarBlank, CalendarCheck, CalendarDots, Check, Clock, Coffee, Info, X } from '@phosphor-icons/react'
import { ApiError, createDoctorSchedule } from '../api'
import type { Department } from '../types'
import { formatDate, formatTimeOfDay } from '../format'
import AdminDatePicker from './AdminDatePicker'
import SlotsTimeline from './SlotsTimeline'
import ScheduleConflictPanel from './ScheduleConflictPanel'
import {
  DAY_NAMES,
  blockSegments,
  checkScheduleConflicts,
  countSlots,
  dateToDayOfWeek,
  enumerateOccurrences,
  mergeConflictChecks,
  timelineForBlocks,
  type ScheduleBlock,
} from './doctorSchedule'

type DuplicateScope = 'this_date' | 'selected_days' | 'custom_range'
type Step = 1 | 2 | 3 | 'success'

// Duplicate Schedule -- a separate, focused workflow for "I already
// have a schedule that works, I want the same one somewhere else",
// opened from Configure Schedule's edit-mode "Schedule actions" menu
// (ScheduleGrid.tsx owns the actual open/close state, same pattern as
// Configure Schedule itself). The source schedule (sourceGroup) is
// read-only here -- if the admin wants different hours, they cancel
// and use Edit Schedule instead, per the product decision to never let
// Duplicate turn into a second editing workflow.
//
// Reuses the exact same building blocks Configure Schedule does: the
// day-multiselect/AdminDatePicker scope inputs, SlotsTimeline for the
// preview, blockSegments + createDoctorSchedule for persistence, and
// checkScheduleConflicts/enumerateOccurrences (doctorSchedule.ts) for
// conflict detection -- no second scheduling engine, no second
// validation system.
export default function DuplicateScheduleModal({
  doctorId,
  departments,
  sourceGroup,
  sourceDate,
  blocks,
  defaultDuration,
  bufferMinutes,
  onClose,
  onSaved,
}: {
  doctorId: number
  departments: Department[]
  sourceGroup: ScheduleBlock[]
  sourceDate: string
  blocks: ScheduleBlock[]
  defaultDuration: number
  bufferMinutes: number
  onClose: () => void
  onSaved: () => void
}) {
  const [step, setStep] = useState<Step>(1)
  const [scope, setScope] = useState<DuplicateScope | null>(null)
  const [targetDate, setTargetDate] = useState('')
  const [weekdays, setWeekdays] = useState<number[]>([])
  const [rangeStart, setRangeStart] = useState('')
  const [rangeEnd, setRangeEnd] = useState('')
  const [departmentId, setDepartmentId] = useState(
    sourceGroup[0]?.departmentId ? String(sourceGroup[0].departmentId) : '',
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedSummary, setSavedSummary] = useState('')

  function toggleWeekday(day: number) {
    setWeekdays((prev) => (prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort((a, b) => a - b)))
  }

  const scopeValid =
    scope === 'this_date'
      ? !!targetDate
      : scope === 'selected_days'
        ? weekdays.length > 0
        : scope === 'custom_range'
          ? weekdays.length > 0 && !!rangeStart && !!rangeEnd && rangeStart <= rangeEnd
          : false

  // Only bounded scopes have an enumerable date list -- "Selected
  // weekdays" is deliberately unbounded (same semantics as Configure
  // Schedule's own create-mode "Repeat weekly"/"Selected days"
  // scopes), so there's no finite list to walk for it.
  const destinationDates: string[] | null =
    scope === 'this_date' && targetDate
      ? [targetDate]
      : scope === 'custom_range' && weekdays.length > 0 && rangeStart && rangeEnd && rangeStart <= rangeEnd
        ? enumerateOccurrences(weekdays, rangeStart, rangeEnd)
        : null

  const destinationCount = destinationDates ? destinationDates.length : null
  const destinationWeekdayList = weekdays.map((d) => DAY_NAMES[d]).join(', ')

  const destinationSummary: string | null =
    scope === 'this_date' && targetDate
      ? formatDate(targetDate)
      : scope === 'selected_days' && weekdays.length > 0
        ? `${destinationWeekdayList}, with no end date`
        : scope === 'custom_range' && destinationCount !== null
          ? `${destinationWeekdayList} · ${formatDate(rangeStart)} – ${formatDate(rangeEnd)} (${destinationCount} date${destinationCount === 1 ? '' : 's'})`
          : null

  // Conflicts -- the same checkScheduleConflicts the Configure Schedule
  // modal uses for Add/Edit, one call per destination weekday, merged.
  // Never excludes any existing rows (duplicating never replaces
  // anything) -- reuses the exact same overlap rule the backend itself
  // enforces, so a conflict caught here is a real one.
  const sourceSegments0 = sourceGroup.flatMap((b) => blockSegments(b.startTime, b.endTime, b.breaks))
  const conflictTargets: { day: number; startDate: string | null; endDate: string | null }[] =
    scope === 'this_date' && targetDate
      ? [{ day: dateToDayOfWeek(targetDate), startDate: targetDate, endDate: targetDate }]
      : scope === 'selected_days'
        ? weekdays.map((day) => ({ day, startDate: null, endDate: null }))
        : scope === 'custom_range' && rangeStart && rangeEnd
          ? weekdays.map((day) => ({ day, startDate: rangeStart, endDate: rangeEnd }))
          : []
  const conflictCheck =
    conflictTargets.length === 0 || sourceSegments0.length === 0
      ? { conflicts: [], conflictDates: new Set<string>(), totalDates: null }
      : mergeConflictChecks(
          conflictTargets.map((t) => checkScheduleConflicts(blocks, [], t.day, sourceSegments0, t.startDate, t.endDate)),
        )
  const hasConflicts = conflictCheck.conflicts.length > 0

  const sourceSegments = timelineForBlocks(sourceGroup, defaultDuration, bufferMinutes)
  const sourceTotal = countSlots(sourceSegments).total
  const sourceHasBreaks = sourceGroup.some((b) => b.breaks.length > 0)

  function departmentName(id: string): string {
    if (!id) return 'All departments'
    return departments.find((d) => d.id === Number(id))?.name ?? 'All departments'
  }

  async function handleDuplicate() {
    setBusy(true)
    setError(null)
    const departmentIdNum = departmentId ? Number(departmentId) : null
    function payloadsFor(day: number, startDate: string | null, endDate: string | null) {
      return sourceGroup.flatMap((b) =>
        blockSegments(b.startTime, b.endTime, b.breaks).map((seg) => ({
          day_of_week: day,
          start_time: seg.start,
          end_time: seg.end,
          start_date: startDate,
          end_date: endDate,
          department_id: departmentIdNum,
        })),
      )
    }
    const payloads =
      scope === 'selected_days'
        ? weekdays.flatMap((day) => payloadsFor(day, null, null))
        : (destinationDates ?? []).flatMap((d) => payloadsFor(dateToDayOfWeek(d), d, d))
    let createdCount = 0
    try {
      for (const payload of payloads) {
        await createDoctorSchedule(doctorId, payload)
        createdCount++
      }
      setSavedSummary(destinationSummary ?? '')
      onSaved()
      setStep('success')
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Could not duplicate this schedule'
      setError(
        createdCount > 0
          ? `${msg} -- ${createdCount} of ${payloads.length} entries were already created before this happened. Check the calendar, then retry for the remaining dates.`
          : `${msg} -- nothing was duplicated.`,
      )
    } finally {
      setBusy(false)
    }
  }

  const stepLabels = ['Select Dates', 'Preview', 'Confirm']
  const displayStep = typeof step === 'number' ? step : stepLabels.length

  return createPortal(
    <div className="modal-overlay schedule-modal-overlay" onClick={onClose}>
      <div
        className="modal-panel schedule-modal-panel schedule-modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Duplicate Schedule"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <div className="schedule-modal-header">
          <div className="schedule-modal-header-top">
            <h3 className="appointment-details-heading" style={{ margin: 0 }}>Duplicate Schedule</h3>
          </div>
          {step !== 'success' && (
            <p className="muted schedule-modal-subtitle">Copy this schedule to other dates</p>
          )}
          <div className="schedule-modal-stepper" aria-label={step === 'success' ? 'All steps complete' : `Step ${displayStep} of ${stepLabels.length}`}>
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

        {step === 1 && (
          <div className="schedule-modal-body schedule-modal-review">
            <div className="schedule-review-summary">
              <div className="schedule-preview-heading">
                <CalendarBlank size={16} />
                <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Source Schedule (Read only)</p>
              </div>
              <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                {new Date(`${sourceDate}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
              </p>
              <dl className="schedule-summary-list">
                <dt>Working hours</dt>
                <dd>
                  {sourceGroup.map((b) => (
                    <div key={b.key}>{formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}</div>
                  ))}
                </dd>
                {sourceHasBreaks && (
                  <>
                    <dt>Break</dt>
                    <dd>
                      {sourceGroup.flatMap((b) => b.breaks).map((br, i) => (
                        <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                      ))}
                    </dd>
                  </>
                )}
                <dt>Department</dt>
                <dd>{departmentName(sourceGroup[0]?.departmentId ? String(sourceGroup[0].departmentId) : '')}</dd>
                <dt>Generated slots</dt>
                <dd>{sourceTotal} slots per day</dd>
              </dl>
              <p className="schedule-scope-summary" style={{ marginTop: 'var(--space-2)' }}>
                <Info size={16} weight="fill" />
                <span>This is the schedule that will be copied. To make changes, use Edit Schedule instead.</span>
              </p>
            </div>

            <div className="schedule-review-preview">
              <span className="field-label">Where should this schedule be applied?</span>
              <div className="schedule-scope-cards" role="radiogroup" aria-label="Duplicate scope">
                {(
                  [
                    { id: 'this_date', icon: CalendarBlank, title: 'This date only', description: 'Select a single date' },
                    { id: 'selected_days', icon: CalendarCheck, title: 'Selected weekdays', description: 'Choose which days of the week' },
                    { id: 'custom_range', icon: CalendarDots, title: 'Custom date range', description: 'Select a date range and optional weekdays' },
                  ] as { id: DuplicateScope; icon: typeof CalendarBlank; title: string; description: string }[]
                ).map((opt) => {
                  const OptIcon = opt.icon
                  const selected = scope === opt.id
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      className={`schedule-scope-card${selected ? ' selected' : ''}`}
                      onClick={() => setScope(opt.id)}
                    >
                      <OptIcon size={22} weight={selected ? 'fill' : 'regular'} />
                      <span className="schedule-scope-card-title">{opt.title}</span>
                      <span className="schedule-scope-card-desc">{opt.description}</span>
                    </button>
                  )
                })}
              </div>

              {scope === 'this_date' && (
                <div className="schedule-field-group">
                  <span className="field-label">Copy to date</span>
                  <AdminDatePicker value={targetDate} onChange={setTargetDate} placeholder="Select date" />
                </div>
              )}

              {(scope === 'selected_days' || scope === 'custom_range') && (
                <div className="schedule-field-group">
                  <span className="field-label">{scope === 'custom_range' ? 'Apply only on' : 'Which days?'}</span>
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
                    <AdminDatePicker value={rangeStart} onChange={setRangeStart} placeholder="Select start date" />
                  </div>
                  <div className="schedule-field-group">
                    <span className="field-label">End date</span>
                    <AdminDatePicker value={rangeEnd} onChange={setRangeEnd} placeholder="Select end date" />
                  </div>
                </div>
              )}

              {destinationSummary && (
                <p className="schedule-scope-summary" style={{ marginTop: 'var(--space-2)' }}>
                  <Info size={16} weight="fill" />
                  <span>
                    Will create schedule for: <strong>{destinationSummary}</strong>
                  </span>
                </p>
              )}

              <label className="schedule-field-group" style={{ marginTop: 'var(--space-2)' }}>
                <span className="field-label">Department</span>
                <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
                  <option value="">All departments</option>
                  {departments.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
                <span className="muted schedule-sidebar-note">Inherited from the source schedule -- change if needed.</span>
              </label>
            </div>

            <p className="muted schedule-sidebar-note" style={{ gridColumn: '1 / -1', margin: 0 }}>
              The copied schedule will not overwrite existing schedules. If there are conflicts, you'll be shown a
              list of affected dates in the next step.
            </p>

            <div className="schedule-modal-footer schedule-modal-footer-full">
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>Cancel</button>
              <button type="button" className="btn btn-sm" disabled={!scopeValid} onClick={() => setStep(2)}>
                Next
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="schedule-modal-body">
            {hasConflicts ? (
              <ScheduleConflictPanel
                conflicts={conflictCheck.conflicts}
                totalDates={conflictCheck.totalDates}
                conflictDateCount={conflictCheck.conflictDates.size}
                newSummary={{
                  icon: CalendarBlank,
                  label: 'Destination',
                  lines: [
                    `Working hours: ${sourceGroup.map((b) => `${formatTimeOfDay(b.startTime)} – ${formatTimeOfDay(b.endTime)}`).join(', ')}`,
                    `Copy to: ${destinationSummary ?? ''}`,
                  ],
                }}
                footer={
                  <div className="schedule-modal-footer schedule-modal-footer-full">
                    <button type="button" className="btn-secondary btn btn-sm" onClick={onClose} disabled={busy}>
                      Cancel
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => setStep(1)} disabled={busy}>
                      Change Dates
                    </button>
                  </div>
                }
              />
            ) : (
              <>
                <h4 className="schedule-review-summary-title">Preview</h4>
                <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                  Source: {formatDate(sourceDate)} · Copy to: {destinationSummary}
                </p>

                <p className="schedule-scope-summary">
                  <Info size={16} weight="fill" />
                  <span>No conflicts found -- {destinationCount !== null ? `${destinationCount} date${destinationCount === 1 ? '' : 's'}` : 'these weekdays'} will be created.</span>
                </p>

                {destinationDates && destinationDates.length > 0 && (
                  <div className="schedule-field-group">
                    <span className="field-label">Affected dates</span>
                    <div className="day-multiselect readonly" role="group" aria-label="Affected dates">
                      {destinationDates.map((d) => (
                        <span key={d} className="selected">
                          {formatDate(d)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <div className="schedule-preview-heading" style={{ marginTop: 'var(--space-3)' }}>
                  <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Schedule preview</p>
                </div>
                <SlotsTimeline segments={sourceSegments} />

                <div className="schedule-modal-footer schedule-modal-footer-full">
                  <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(1)} disabled={busy}>
                    Back
                  </button>
                  <button type="button" className="btn-secondary btn btn-sm" onClick={onClose} disabled={busy}>
                    Cancel
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => setStep(3)} disabled={busy}>
                    Next
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="schedule-modal-body">
            <h4 className="schedule-review-summary-title">Confirm Duplication</h4>
            <div className="schedule-summary-cards">
              <div className="schedule-summary-card">
                <CalendarBlank size={18} />
                <div className="schedule-summary-card-body">
                  <span className="schedule-summary-card-label">Source</span>
                  <span className="schedule-summary-card-value">
                    {new Date(`${sourceDate}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
                  </span>
                </div>
              </div>
              <div className="schedule-summary-card">
                <CalendarBlank size={18} />
                <div className="schedule-summary-card-body">
                  <span className="schedule-summary-card-label">Destination</span>
                  <span className="schedule-summary-card-value">{destinationSummary}</span>
                </div>
              </div>
              <div className="schedule-summary-card">
                <Clock size={18} />
                <div className="schedule-summary-card-body">
                  <span className="schedule-summary-card-label">Working hours</span>
                  <span className="schedule-summary-card-value">
                    {sourceGroup.map((b) => (
                      <div key={b.key}>{formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}</div>
                    ))}
                  </span>
                </div>
              </div>
              {sourceHasBreaks && (
                <div className="schedule-summary-card">
                  <Coffee size={18} />
                  <div className="schedule-summary-card-body">
                    <span className="schedule-summary-card-label">Break</span>
                    <span className="schedule-summary-card-value">
                      {sourceGroup.flatMap((b) => b.breaks).map((br, i) => (
                        <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                      ))}
                    </span>
                  </div>
                </div>
              )}
              <div className="schedule-summary-card">
                <Buildings size={18} />
                <div className="schedule-summary-card-body">
                  <span className="schedule-summary-card-label">Department</span>
                  <span className="schedule-summary-card-value">{departmentName(departmentId)}</span>
                </div>
              </div>
            </div>

            <p className="schedule-scope-summary schedule-review-headline">
              <Info size={16} weight="fill" />
              <span>
                {destinationCount !== null
                  ? `${destinationCount} date${destinationCount === 1 ? '' : 's'} affected.`
                  : `This applies to ${destinationWeekdayList}, with no end date.`}
              </span>
            </p>

            <div className="schedule-modal-footer schedule-modal-footer-full">
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(2)} disabled={busy}>
                Back
              </button>
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-sm" onClick={handleDuplicate} disabled={busy}>
                {busy ? 'Duplicating…' : 'Duplicate Schedule'}
              </button>
            </div>
          </div>
        )}

        {step === 'success' && (
          <div className="schedule-modal-body schedule-modal-success">
            <div className="schedule-success-check" aria-hidden="true">{'✓'}</div>
            <h3 style={{ margin: '0 0 4px' }}>Schedule duplicated successfully!</h3>
            <p className="muted" style={{ margin: 0 }}>The schedule was copied to:</p>
            <div className="schedule-summary-card success">
              <CalendarBlank size={18} />
              <div className="schedule-summary-card-body">
                <span className="schedule-summary-card-value schedule-success-headline">{savedSummary}</span>
              </div>
            </div>
            <div className="schedule-modal-footer">
              <button type="button" className="btn btn-sm" onClick={onClose}>View Calendar</button>
              <button
                type="button" className="btn-secondary btn btn-sm"
                onClick={() => {
                  setStep(1); setScope(null); setTargetDate(''); setWeekdays([]); setRangeStart(''); setRangeEnd('')
                  setError(null)
                }}
              >
                Duplicate Again
              </button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
