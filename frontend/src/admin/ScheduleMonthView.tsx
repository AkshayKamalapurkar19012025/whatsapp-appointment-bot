import { useRef, useState } from 'react'
import { CalendarBlank, Gear } from '@phosphor-icons/react'
import type { Department, DoctorBlockEntry } from '../types'
import { formatTimeOfDay } from '../format'
import { blockCoversDate, dateInRange, dateToDayOfWeek, type ScheduleBlock } from './doctorSchedule'
import SlotSettingsFields from './SlotSettingsFields'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

// Monday-first column (0..6) for the 1st of the month.
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
  blocks: ScheduleBlock[],
  oneOffBlocks: DoctorBlockEntry[],
): { status: DayStatus; blocks: ScheduleBlock[] } {
  const applicable = blocks
    .filter((b) => b.day === dayOfWeek && dateInRange(dateStr, b.startDate, b.endDate))
    .sort((a, b) => a.startTime.localeCompare(b.startTime))
  if (oneOffBlocks.some((b) => blockCoversDate(b, dateStr))) {
    return { status: 'time_off', blocks: applicable }
  }
  if (applicable.length === 0) return { status: 'none', blocks: [] }
  if (applicable.length === 1 && applicable[0].breaks.length === 0) return { status: 'working', blocks: applicable }
  return { status: 'partial', blocks: applicable }
}

// Monthly calendar for the Schedule tab -- a pure VIEW of the doctor's
// schedule. Every mutation (create a schedule, edit or remove an
// existing one) happens in the Configure Schedule popup (ScheduleGrid.tsx
// owns opening it, since it needs the full unfiltered block list to
// compute the edit target); this component only renders the calendar,
// the toolbar, and the compact Slot settings popover, and reports which
// date was clicked or that "+ Add Schedule" was pressed. See
// ScheduleGrid.tsx's own top-of-file comment for the full workflow.
export default function ScheduleMonthView({
  blocks,
  oneOffBlocks,
  departments,
  defaultDuration,
  bufferMinutes,
  isAdmin,
  onDateClick,
  onAddSchedule,
  onChangeDuration,
  onChangeBuffer,
}: {
  blocks: ScheduleBlock[]
  oneOffBlocks: DoctorBlockEntry[]
  departments: Department[]
  defaultDuration: number
  bufferMinutes: number
  isAdmin: boolean
  onDateClick: (dateStr: string) => void
  onAddSchedule: () => void
  onChangeDuration: (minutes: number) => void
  onChangeBuffer: (minutes: number) => void
}) {
  const today = new Date()
  const todayIso = isoDate(today.getFullYear(), today.getMonth() + 1, today.getDate())
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)
  const [jumpOpen, setJumpOpen] = useState(false)
  const [slotSettingsOpen, setSlotSettingsOpen] = useState(false)
  const [filterDepartmentId, setFilterDepartmentId] = useState('')
  // Briefly rings today's cell after "Today" is clicked, so jumping back
  // to the current month makes it immediately obvious which date that
  // is -- not a persistent selection state (this calendar doesn't have
  // one anymore; clicking a date opens the Configure Schedule popup
  // straight away), just a momentary visual confirmation.
  const [highlightedDate, setHighlightedDate] = useState<string | null>(null)
  const highlightTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Department filter -- real (blocks are actually department_id-scoped
  // in doctor_schedule, migrations/0010), a view-only lens on the
  // calendar; doesn't affect what a date click edits.
  const visibleBlocks = filterDepartmentId
    ? blocks.filter((b) => b.departmentId === Number(filterDepartmentId))
    : blocks

  function goPrevMonth() {
    setMonth((m) => (m === 1 ? (setYear((y) => y - 1), 12) : m - 1))
  }
  function goNextMonth() {
    setMonth((m) => (m === 12 ? (setYear((y) => y + 1), 1) : m + 1))
  }
  function goToday() {
    setYear(today.getFullYear())
    setMonth(today.getMonth() + 1)
    if (highlightTimeoutRef.current) clearTimeout(highlightTimeoutRef.current)
    setHighlightedDate(todayIso)
    highlightTimeoutRef.current = setTimeout(() => setHighlightedDate(null), 2000)
  }

  const yearOptions = Array.from({ length: 6 }, (_, i) => today.getFullYear() - 2 + i)

  const totalDays = daysInMonth(year, month)
  const leadingBlanks = firstWeekdayColumn(year, month)
  const cells: (number | null)[] = [
    ...Array.from({ length: leadingBlanks }, () => null),
    ...Array.from({ length: totalDays }, (_, i) => i + 1),
  ]
  while (cells.length % 7 !== 0) cells.push(null)

  return (
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

        <div className="schedule-month-toolbar-spacer" />

        {isAdmin && (
          <div className="schedule-month-jump">
            <button
              type="button"
              className="btn-secondary btn btn-sm"
              onClick={() => setSlotSettingsOpen((v) => !v)}
            >
              <Gear size={16} />
              Slot settings
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
        )}
        {isAdmin && (
          <button type="button" className="btn btn-sm" onClick={onAddSchedule}>
            + Add Schedule
          </button>
        )}
      </div>

      <div className="schedule-month-legend">
        <span className="schedule-legend-dot working" /> Working day
        <span className="schedule-legend-dot partial" /> Partial day
        <span className="schedule-legend-dot time_off" /> Time off
        <span className="schedule-legend-dot none" /> No schedule
      </div>

      <div className="schedule-month-grid">
        {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((name) => (
          <div key={name} className="schedule-month-weekday">
            {name.slice(0, 3)}
          </div>
        ))}
        {cells.map((day, i) => {
          if (day === null) return <div key={i} className="schedule-month-cell empty" />
          const dateStr = isoDate(year, month, day)
          const dayOfWeek = dateToDayOfWeek(dateStr)
          const { status, blocks: dateBlocks } = statusForDate(dateStr, dayOfWeek, visibleBlocks, oneOffBlocks)
          const isTimeOff = status === 'time_off'
          const overflow = dateBlocks.length - 2
          return (
            <button
              key={i}
              type="button"
              className={`schedule-month-cell${dateStr === highlightedDate ? ' highlighted' : ''}`}
              onClick={() => onDateClick(dateStr)}
            >
              <span className="schedule-month-cell-date">{day}</span>
              {isTimeOff ? (
                <span className="schedule-month-cell-timeoff">Time off</span>
              ) : dateBlocks.length > 0 ? (
                <span className="schedule-month-cell-periods">
                  {dateBlocks.slice(0, 2).map((b, bi) => (
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
  )
}
