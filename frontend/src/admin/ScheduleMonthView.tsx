import { useState } from 'react'
import { CalendarBlank } from '@phosphor-icons/react'
import type { Department, DoctorBlockEntry } from '../types'
import { formatDate, formatTimeOfDay } from '../format'
import { TimeCombobox } from '../components/ui/time-combobox'
import AdminDatePicker from './AdminDatePicker'
import {
  DAY_NAMES,
  blockCoversDate,
  countOccurrences,
  dateInRange,
  dateToDayOfWeek,
  defaultBreakFor,
  templateDaySlots,
  validateBlockBreaks,
  type ScheduleBlock,
  type ScheduleBreak,
} from './doctorSchedule'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

// Monday-first column (0..6) for the 1st of the month -- same convention
// the weekly grid and DAY_NAMES already use throughout this file, kept
// even though the reference mockup this view is scoped from happened to
// start its week on Sunday (a visual detail, not part of what was
// approved to copy -- see the PLAN's own "UX/scope reference, not a
// visual template" instruction).
function firstWeekdayColumn(year: number, month: number): number {
  const jsDay = new Date(year, month - 1, 1).getDay()
  return (jsDay + 6) % 7
}

function isoDate(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

type DayStatus = 'time_off' | 'working' | 'partial' | 'none'

function statusForDate(
  dateStr: string,
  dayOfWeek: number,
  draftBlocks: ScheduleBlock[],
  oneOffBlocks: DoctorBlockEntry[],
): { status: DayStatus; blocks: ScheduleBlock[] } {
  const applicable = draftBlocks
    .filter((b) => b.day === dayOfWeek && dateInRange(dateStr, b.startDate, b.endDate))
    .sort((a, b) => a.startTime.localeCompare(b.startTime))
  if (oneOffBlocks.some((b) => blockCoversDate(b, dateStr))) {
    return { status: 'time_off', blocks: applicable }
  }
  if (applicable.length === 0) return { status: 'none', blocks: [] }
  if (applicable.length === 1 && applicable[0].breaks.length === 0) return { status: 'working', blocks: applicable }
  return { status: 'partial', blocks: applicable }
}

// A block's own date-range label, for the small "applies to" note under
// each period -- distinguishes a one-off single date from an open-ended
// or bounded recurring pattern, without forcing the range pickers into
// view for the common case.
function rangeLabel(b: ScheduleBlock, dateStr: string): string {
  if (b.startDate === dateStr && b.endDate === dateStr) return 'Just this day'
  if (!b.startDate && !b.endDate) return `Every ${DAY_NAMES[b.day]}`
  const start = b.startDate ? formatDate(b.startDate) : 'always'
  const end = b.endDate ? formatDate(b.endDate) : 'always'
  return `Every ${DAY_NAMES[b.day]}, ${start} – ${end}`
}

type Scope = 'this_date' | 'every_weekday' | 'selected_days' | 'custom_range'

// The Add Schedule flow -- Step 1 (explicit scope: no recurrence is ever
// implicit) then Step 2 (hours/breaks/department), both inline in the
// day panel rather than a separate modal form. Only materializes real
// ScheduleBlocks (via onCreate) once the admin confirms; nothing is
// added to the draft while choosing/configuring.
function AddScheduleWizard({
  dateStr,
  departments,
  defaultDuration,
  bufferMinutes,
  onCreate,
  onCancel,
}: {
  dateStr: string
  departments: Department[]
  defaultDuration: number
  bufferMinutes: number
  onCreate: (
    weekdays: number[],
    startDate: string | null,
    endDate: string | null,
    startTime: string,
    endTime: string,
    breaks: ScheduleBreak[],
    departmentId: number | null,
  ) => void
  onCancel: () => void
}) {
  const clickedWeekday = dateToDayOfWeek(dateStr)
  const [scope, setScope] = useState<Scope | null>(null)
  const [weekdays, setWeekdays] = useState<number[]>([clickedWeekday])
  const [rangeStart, setRangeStart] = useState(dateStr)
  const [rangeEnd, setRangeEnd] = useState('')
  const [startTime, setStartTime] = useState('09:00')
  const [endTime, setEndTime] = useState('17:00')
  const [breaks, setBreaks] = useState<ScheduleBreak[]>([])
  const [departmentId, setDepartmentId] = useState('')

  function toggleWeekday(day: number) {
    setWeekdays((prev) => (prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort((a, b) => a - b)))
  }

  const resolvedWeekdays = scope === 'this_date' || scope === 'every_weekday' ? [clickedWeekday] : weekdays
  const resolvedStartDate = scope === 'this_date' ? dateStr : scope === 'custom_range' ? rangeStart || null : null
  const resolvedEndDate = scope === 'this_date' ? dateStr : scope === 'custom_range' ? rangeEnd || null : null

  const scopeValid =
    scope === 'this_date' ||
    scope === 'every_weekday' ||
    (scope === 'selected_days' && weekdays.length > 0) ||
    (scope === 'custom_range' && weekdays.length > 0 && !!rangeStart && !!rangeEnd && rangeStart <= rangeEnd)

  const timeError = !(startTime < endTime)
    ? 'End time must be after start time'
    : validateBlockBreaks(startTime, endTime, breaks)

  const previewBlock: ScheduleBlock = {
    key: 'preview',
    day: resolvedWeekdays[0] ?? clickedWeekday,
    startTime,
    endTime,
    breaks,
    departmentId: null,
    startDate: null,
    endDate: null,
    sourceIds: [],
  }
  const previewSlots = timeError ? [] : templateDaySlots([previewBlock], defaultDuration, bufferMinutes)

  const occurrenceCount =
    resolvedStartDate && resolvedEndDate ? countOccurrences(resolvedWeekdays, resolvedStartDate, resolvedEndDate) : null
  const weekdayList = resolvedWeekdays.map((d) => DAY_NAMES[d]).join(', ')
  const summary =
    occurrenceCount !== null
      ? `${weekdayList}, ${formatDate(resolvedStartDate!)} – ${formatDate(resolvedEndDate!)} (${occurrenceCount} date${occurrenceCount === 1 ? '' : 's'})`
      : `Every ${weekdayList}`

  function handleCreate() {
    if (!scopeValid || timeError) return
    onCreate(resolvedWeekdays, resolvedStartDate, resolvedEndDate, startTime, endTime, breaks, departmentId ? Number(departmentId) : null)
    onCancel()
  }

  return (
    <div className="schedule-day-period schedule-add-wizard">
      {scope === null ? (
        <>
          <span className="field-label">How should this schedule apply?</span>
          <div className="schedule-scope-choices" role="radiogroup" aria-label="Schedule scope">
            <label className="schedule-scope-option">
              <input type="radio" name="schedule-scope" onChange={() => setScope('this_date')} />
              This day only ({formatDate(dateStr)})
            </label>
            <label className="schedule-scope-option">
              <input type="radio" name="schedule-scope" onChange={() => setScope('every_weekday')} />
              Every {DAY_NAMES[clickedWeekday]} (recurring)
            </label>
            <label className="schedule-scope-option">
              <input type="radio" name="schedule-scope" onChange={() => setScope('selected_days')} />
              Selected days
            </label>
            <label className="schedule-scope-option">
              <input type="radio" name="schedule-scope" onChange={() => setScope('custom_range')} />
              Custom date range
            </label>
          </div>
          <div className="schedule-save-bar-actions">
            <button type="button" className="btn-secondary btn btn-sm" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          {(scope === 'selected_days' || scope === 'custom_range') && (
            <div className="schedule-field-group">
              <span className="field-label">Which days?</span>
              <div className="day-multiselect" role="group" aria-label="Weekdays">
                {DAY_NAMES.slice(1).map((name, i) => {
                  const day = i + 1
                  return (
                    <button
                      key={day}
                      type="button"
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
                <AdminDatePicker value={rangeStart} onChange={setRangeStart} label="Pick start date" />
              </div>
              <div className="schedule-field-group">
                <span className="field-label">End date</span>
                <AdminDatePicker value={rangeEnd} onChange={setRangeEnd} label="Pick end date" />
              </div>
            </div>
          )}

          <div className="schedule-field-grid">
            <label className="schedule-field-group">
              <span className="field-label">Start</span>
              <TimeCombobox value={startTime} onChange={setStartTime} durationMinutes={defaultDuration} ariaLabel="New schedule start time" />
            </label>
            <label className="schedule-field-group">
              <span className="field-label">End</span>
              <TimeCombobox value={endTime} onChange={setEndTime} durationMinutes={defaultDuration} ariaLabel="New schedule end time" />
            </label>
            <label className="schedule-field-group">
              <span className="field-label">Department</span>
              <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
                <option value="">All departments</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {timeError && <p className="error">{timeError}</p>}

          <div className="schedule-field-group schedule-breaks">
            <span className="field-label">Breaks</span>
            {breaks.map((br, i) => (
              <div key={i} className="schedule-break-row">
                <TimeCombobox
                  value={br.start}
                  onChange={(v) => setBreaks((prev) => prev.map((x, xi) => (xi === i ? { ...x, start: v } : x)))}
                  durationMinutes={defaultDuration}
                  ariaLabel={`Break ${i + 1} start`}
                />
                <span className="arrow">{'→'}</span>
                <TimeCombobox
                  value={br.end}
                  onChange={(v) => setBreaks((prev) => prev.map((x, xi) => (xi === i ? { ...x, end: v } : x)))}
                  durationMinutes={defaultDuration}
                  ariaLabel={`Break ${i + 1} end`}
                />
                <button type="button" className="link danger" onClick={() => setBreaks((prev) => prev.filter((_, xi) => xi !== i))}>
                  Remove
                </button>
              </div>
            ))}
            <button type="button" className="link" onClick={() => setBreaks((prev) => [...prev, defaultBreakFor(startTime, endTime)])}>
              + Add a break
            </button>
          </div>

          <div className="schedule-preview-heading">
            <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>
              Generated slots preview
            </p>
          </div>
          {previewSlots.length > 0 ? (
            <div className="schedule-preview-slots">
              {previewSlots.map((s, i) => (
                <span key={i} className="slot-chip-static">
                  {s}
                </span>
              ))}
            </div>
          ) : (
            <span className="muted schedule-sidebar-note">No slots</span>
          )}

          <p className="muted schedule-sidebar-note schedule-scope-summary">
            <strong>This schedule will apply to:</strong> {summary}
          </p>

          <div className="schedule-save-bar-actions">
            <button type="button" className="btn-secondary btn btn-sm" onClick={() => setScope(null)}>
              Back
            </button>
            <button type="button" className="btn btn-sm" onClick={handleCreate} disabled={!scopeValid || !!timeError}>
              Add schedule
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// Monthly calendar for the Schedule tab -- the primary scheduling
// workspace: the admin selects a date, explicitly chooses a scope for
// any new schedule (This day only / Every weekday / Selected days /
// Custom date range -- never an implicit default), configures
// hours/breaks/department, sees a live preview and an explicit "applies
// to N dates" summary, and only then creates it. Existing periods stay
// directly editable inline; editing or removing one that spans more
// than the clicked date triggers onEditBlock/onRemoveBlock's own
// this-date/this-and-future/entire-schedule prompt (ScheduleGrid.tsx)
// rather than silently changing the whole pattern. The weekly grid
// (ScheduleGrid.tsx's own view) remains available as an advanced/bulk
// option but is never required for normal day-to-day schedule edits.
// Time off is read-only context here (layered from doctor_blocks) --
// editing it stays on the separate Time off tab, not duplicated.
export default function ScheduleMonthView({
  draftBlocks,
  oneOffBlocks,
  departments,
  defaultDuration,
  bufferMinutes,
  overallDirty,
  isAdmin,
  onCreateSchedule,
  onEditBlock,
  onRemoveBlock,
  onCopyFromDate,
}: {
  draftBlocks: ScheduleBlock[]
  oneOffBlocks: DoctorBlockEntry[]
  departments: Department[]
  defaultDuration: number
  bufferMinutes: number
  overallDirty: boolean
  isAdmin: boolean
  onCreateSchedule: (
    weekdays: number[],
    startDate: string | null,
    endDate: string | null,
    startTime: string,
    endTime: string,
    breaks: ScheduleBreak[],
    departmentId: number | null,
  ) => void
  onEditBlock: (block: ScheduleBlock, dateStr: string, mutator: (b: ScheduleBlock) => ScheduleBlock) => void
  onRemoveBlock: (block: ScheduleBlock, dateStr: string) => void
  onCopyFromDate: (dateStr: string) => void
}) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [jumpOpen, setJumpOpen] = useState(false)
  const [filterDepartmentId, setFilterDepartmentId] = useState('')
  const [rangeEditingKeys, setRangeEditingKeys] = useState<Set<string>>(new Set())
  const [addingSchedule, setAddingSchedule] = useState(false)

  // Department filter -- real (blocks are actually department_id-scoped
  // in doctor_schedule, migrations/0010), unlike an "Appointment type"
  // filter would be: doctor_schedule has no appointment-type dimension
  // at all, so that control from the reference mockup was left out
  // rather than shipped as a dropdown that silently filters nothing.
  const visibleBlocks = filterDepartmentId
    ? draftBlocks.filter((b) => b.departmentId === Number(filterDepartmentId))
    : draftBlocks

  function goPrevMonth() {
    setMonth((m) => (m === 1 ? (setYear((y) => y - 1), 12) : m - 1))
  }
  function goNextMonth() {
    setMonth((m) => (m === 12 ? (setYear((y) => y + 1), 1) : m + 1))
  }
  function goToday() {
    setYear(today.getFullYear())
    setMonth(today.getMonth() + 1)
    setSelectedDate(null)
  }

  function selectDate(dateStr: string) {
    setSelectedDate(dateStr)
    setAddingSchedule(false)
  }

  const yearOptions = Array.from({ length: 6 }, (_, i) => today.getFullYear() - 2 + i)

  const totalDays = daysInMonth(year, month)
  const leadingBlanks = firstWeekdayColumn(year, month)
  const cells: (number | null)[] = [
    ...Array.from({ length: leadingBlanks }, () => null),
    ...Array.from({ length: totalDays }, (_, i) => i + 1),
  ]
  while (cells.length % 7 !== 0) cells.push(null)

  const selectedInfo = selectedDate
    ? statusForDate(selectedDate, dateToDayOfWeek(selectedDate), visibleBlocks, oneOffBlocks)
    : null
  const selectedIsTimeOff =
    selectedDate !== null && oneOffBlocks.some((b) => blockCoversDate(b, selectedDate))
  const selectedSlots = selectedInfo ? templateDaySlots(selectedInfo.blocks, defaultDuration, bufferMinutes) : []

  function toggleRangeEditing(key: string) {
    setRangeEditingKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <>
      <div className="schedule-month-main">
        <div className="schedule-month-filters">
          <label className="inline-label">
            Department
            <select value={filterDepartmentId} onChange={(e) => setFilterDepartmentId(e.target.value)}>
              <option value="">All departments</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="schedule-month-toolbar">
          <button type="button" className="btn-secondary btn btn-sm" onClick={goPrevMonth} aria-label="Previous month">
            {'‹'}
          </button>
          <span className="schedule-month-title">
            {MONTH_NAMES[month - 1]} {year}
          </span>
          <button type="button" className="btn-secondary btn btn-sm" onClick={goNextMonth} aria-label="Next month">
            {'›'}
          </button>
          <div className="schedule-month-jump">
            <button
              type="button"
              className="btn-secondary btn btn-sm"
              aria-label="Jump to month"
              onClick={() => setJumpOpen((v) => !v)}
            >
              <CalendarBlank size={16} />
            </button>
            {jumpOpen && (
              <div className="schedule-month-jump-popover">
                <select value={month} onChange={(e) => setMonth(Number(e.target.value))}>
                  {MONTH_NAMES.map((name, i) => (
                    <option key={name} value={i + 1}>
                      {name}
                    </option>
                  ))}
                </select>
                <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
                  {yearOptions.map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
                <button type="button" className="btn btn-sm" onClick={() => setJumpOpen(false)}>
                  Go
                </button>
              </div>
            )}
          </div>
          <button type="button" className="btn-secondary btn btn-sm" onClick={goToday}>
            Today
          </button>
          <div className="schedule-month-legend">
            <span className="schedule-legend-dot working" /> Working day
            <span className="schedule-legend-dot partial" /> Partial day
            <span className="schedule-legend-dot time_off" /> Time off
            <span className="schedule-legend-dot none" /> No schedule
          </div>
        </div>

        <div className="schedule-month-grid">
          {DAY_NAMES.slice(1).map((name) => (
            <div key={name} className="schedule-month-weekday">
              {name.slice(0, 3)}
            </div>
          ))}
          {cells.map((day, i) => {
            if (day === null) return <div key={i} className="schedule-month-cell empty" />
            const dateStr = isoDate(year, month, day)
            const dayOfWeek = dateToDayOfWeek(dateStr)
            const { status, blocks } = statusForDate(dateStr, dayOfWeek, visibleBlocks, oneOffBlocks)
            const isTimeOff = status === 'time_off'
            const overflow = blocks.length - 2
            return (
              <button
                key={i}
                type="button"
                className={`schedule-month-cell ${selectedDate === dateStr ? 'selected' : ''}`}
                onClick={() => selectDate(dateStr)}
              >
                <span className="schedule-month-cell-date">{day}</span>
                {isTimeOff ? (
                  <span className="schedule-month-cell-timeoff">Time off</span>
                ) : blocks.length > 0 ? (
                  <span className="schedule-month-cell-periods">
                    {blocks.slice(0, 2).map((b, bi) => (
                      <span key={bi} className={`schedule-month-cell-period ${status}`}>
                        {formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}
                      </span>
                    ))}
                    {overflow > 0 && <span className="schedule-month-cell-more">+{overflow} more</span>}
                  </span>
                ) : (
                  <span className="schedule-month-cell-none">No schedule</span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {selectedDate && (
        <div className="schedule-block-panel">
          <div className="admin-content-header">
            <h4 style={{ margin: 0 }}>
              {new Date(`${selectedDate}T00:00:00`).toLocaleDateString('en-US', {
                weekday: 'long',
                day: 'numeric',
                month: 'short',
                year: 'numeric',
              })}
            </h4>
            {isAdmin && selectedInfo && selectedInfo.blocks.length > 0 && (
              <button type="button" className="link" onClick={() => onCopyFromDate(selectedDate)}>
                Copy to other days
              </button>
            )}
          </div>

          {selectedIsTimeOff && (
            <p className="schedule-month-cell-timeoff standalone">
              Time off this day -- managed from the Time off tab; working hours below still describe the underlying
              pattern.
            </p>
          )}

          {selectedInfo && selectedInfo.blocks.length > 0 ? (
            <>
              <p className="muted" style={{ margin: 0 }}>
                Working hours ({selectedInfo.blocks.length} period{selectedInfo.blocks.length === 1 ? '' : 's'})
              </p>
              {selectedInfo.blocks.map((b) => {
                const blockError = !(b.startTime < b.endTime)
                  ? 'End time must be after start time'
                  : validateBlockBreaks(b.startTime, b.endTime, b.breaks)
                const editingRange = rangeEditingKeys.has(b.key)
                return (
                  <div key={b.key} className="schedule-day-period">
                    <div className="schedule-field-grid">
                      <label className="schedule-field-group">
                        <span className="field-label">Start</span>
                        <TimeCombobox
                          value={b.startTime}
                          onChange={(v) => onEditBlock(b, selectedDate, (bl) => ({ ...bl, startTime: v }))}
                          durationMinutes={defaultDuration}
                          ariaLabel={`Period start, ${formatTimeOfDay(b.startTime)}`}
                        />
                      </label>
                      <label className="schedule-field-group">
                        <span className="field-label">End</span>
                        <TimeCombobox
                          value={b.endTime}
                          onChange={(v) => onEditBlock(b, selectedDate, (bl) => ({ ...bl, endTime: v }))}
                          durationMinutes={defaultDuration}
                          ariaLabel={`Period end, ${formatTimeOfDay(b.endTime)}`}
                        />
                      </label>
                      <label className="schedule-field-group">
                        <span className="field-label">Department</span>
                        <select
                          value={b.departmentId ?? ''}
                          onChange={(e) =>
                            onEditBlock(b, selectedDate, (bl) => ({
                              ...bl,
                              departmentId: e.target.value ? Number(e.target.value) : null,
                            }))
                          }
                        >
                          <option value="">All departments</option>
                          {departments.map((d) => (
                            <option key={d.id} value={d.id}>
                              {d.name}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>

                    {blockError && <p className="error">{blockError}</p>}

                    <div className="schedule-day-period-range">
                      <span className="muted schedule-sidebar-note">{rangeLabel(b, selectedDate)}</span>
                      <button type="button" className="link" onClick={() => toggleRangeEditing(b.key)}>
                        {editingRange ? 'Done' : 'Change dates…'}
                      </button>
                    </div>
                    {editingRange && (
                      <div className="schedule-field-grid">
                        <div className="schedule-field-group">
                          <span className="field-label">Start date</span>
                          <AdminDatePicker
                            value={b.startDate ?? ''}
                            onChange={(v) => onEditBlock(b, selectedDate, (bl) => ({ ...bl, startDate: v || null }))}
                            label="Pick start date"
                          />
                        </div>
                        <div className="schedule-field-group">
                          <span className="field-label">Repeat until</span>
                          <AdminDatePicker
                            value={b.endDate ?? ''}
                            onChange={(v) => onEditBlock(b, selectedDate, (bl) => ({ ...bl, endDate: v || null }))}
                            label="Pick end date"
                          />
                        </div>
                      </div>
                    )}

                    <div className="schedule-field-group schedule-breaks">
                      <span className="field-label">Breaks</span>
                      {b.breaks.map((br, i) => (
                        <div key={i} className="schedule-break-row">
                          <TimeCombobox
                            value={br.start}
                            onChange={(v) =>
                              onEditBlock(b, selectedDate, (bl) => ({
                                ...bl,
                                breaks: bl.breaks.map((x, xi) => (xi === i ? { ...x, start: v } : x)),
                              }))
                            }
                            durationMinutes={defaultDuration}
                            ariaLabel={`Break ${i + 1} start`}
                          />
                          <span className="arrow">{'→'}</span>
                          <TimeCombobox
                            value={br.end}
                            onChange={(v) =>
                              onEditBlock(b, selectedDate, (bl) => ({
                                ...bl,
                                breaks: bl.breaks.map((x, xi) => (xi === i ? { ...x, end: v } : x)),
                              }))
                            }
                            durationMinutes={defaultDuration}
                            ariaLabel={`Break ${i + 1} end`}
                          />
                          <button
                            type="button"
                            className="link danger"
                            onClick={() =>
                              onEditBlock(b, selectedDate, (bl) => ({ ...bl, breaks: bl.breaks.filter((_, xi) => xi !== i) }))
                            }
                          >
                            Remove
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        className="link"
                        onClick={() =>
                          onEditBlock(b, selectedDate, (bl) => ({ ...bl, breaks: [...bl.breaks, defaultBreakFor(bl.startTime, bl.endTime)] }))
                        }
                      >
                        + Add a break
                      </button>
                    </div>

                    <button type="button" className="link danger" onClick={() => onRemoveBlock(b, selectedDate)}>
                      Remove this period
                    </button>
                  </div>
                )
              })}
            </>
          ) : (
            !selectedIsTimeOff && <p className="muted">No working hours set for this day.</p>
          )}

          {isAdmin && addingSchedule && (
            <AddScheduleWizard
              dateStr={selectedDate}
              departments={departments}
              defaultDuration={defaultDuration}
              bufferMinutes={bufferMinutes}
              onCreate={onCreateSchedule}
              onCancel={() => setAddingSchedule(false)}
            />
          )}
          {isAdmin && !addingSchedule && (
            <button type="button" className="btn btn-sm" onClick={() => setAddingSchedule(true)}>
              + Add schedule
            </button>
          )}

          {selectedInfo && selectedInfo.blocks.length > 0 && (
            <>
              <div className="schedule-preview-heading">
                <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>
                  Generated slots preview
                </p>
                {overallDirty && <span className="draft-badge">Previewing unsaved changes</span>}
              </div>
              {selectedSlots.length > 0 ? (
                <div className="schedule-preview-slots">
                  {selectedSlots.map((s, i) => (
                    <span key={i} className="slot-chip-static">
                      {s}
                    </span>
                  ))}
                </div>
              ) : (
                <span className="muted schedule-sidebar-note">No slots</span>
              )}
              <p className="muted schedule-sidebar-note">
                Not a guarantee of real booking availability -- existing appointments and time off aren't excluded
                here.
              </p>
            </>
          )}
        </div>
      )}
    </>
  )
}
