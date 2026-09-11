import { useState } from 'react'
import type { Department, DoctorBlockEntry } from '../types'
import { formatTimeOfDay } from '../format'
import {
  DAY_NAMES,
  blockCoversDate,
  dateInRange,
  dateToDayOfWeek,
  templateDaySlots,
  type ScheduleBlock,
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
  const applicable = draftBlocks.filter((b) => b.day === dayOfWeek && dateInRange(dateStr, b.startDate, b.endDate))
  if (oneOffBlocks.some((b) => blockCoversDate(b, dateStr))) {
    return { status: 'time_off', blocks: applicable }
  }
  if (applicable.length === 0) return { status: 'none', blocks: [] }
  if (applicable.length === 1 && applicable[0].breaks.length === 0) return { status: 'working', blocks: applicable }
  return { status: 'partial', blocks: applicable }
}

// Phase A of the Schedule tab redesign (see the approved PLAN): a
// read-oriented monthly summary of the SAME doctor_schedule-backed
// blocks the weekly grid edits, plus one-off Time off blocks layered on
// top for context. Deliberately not a second editor -- "Edit day" hands
// off to the existing weekly grid's own per-block panel (onEditDay),
// and there is no Copy-schedule or other bulk convenience here, per the
// PLAN's explicit "core loop first" scope.
export default function ScheduleMonthView({
  draftBlocks,
  oneOffBlocks,
  departments,
  defaultDuration,
  bufferMinutes,
  overallDirty,
  onEditDay,
}: {
  draftBlocks: ScheduleBlock[]
  oneOffBlocks: DoctorBlockEntry[]
  departments: Department[]
  defaultDuration: number
  bufferMinutes: number
  overallDirty: boolean
  onEditDay: (dateStr: string) => void
}) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  function departmentName(id: number | null): string {
    if (id === null) return 'All departments'
    return departments.find((d) => d.id === id)?.name ?? 'All departments'
  }

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

  const totalDays = daysInMonth(year, month)
  const leadingBlanks = firstWeekdayColumn(year, month)
  const cells: (number | null)[] = [
    ...Array.from({ length: leadingBlanks }, () => null),
    ...Array.from({ length: totalDays }, (_, i) => i + 1),
  ]
  while (cells.length % 7 !== 0) cells.push(null)

  const selectedInfo = selectedDate
    ? statusForDate(selectedDate, dateToDayOfWeek(selectedDate), draftBlocks, oneOffBlocks)
    : null
  const selectedIsTimeOff =
    selectedDate !== null && oneOffBlocks.some((b) => blockCoversDate(b, selectedDate))
  const selectedSlots = selectedInfo ? templateDaySlots(selectedInfo.blocks, defaultDuration, bufferMinutes) : []

  return (
    <>
      <div className="schedule-month-main">
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
            const { status, blocks } = statusForDate(dateStr, dayOfWeek, draftBlocks, oneOffBlocks)
            const isTimeOff = status === 'time_off'
            return (
              <button
                key={i}
                type="button"
                className={`schedule-month-cell ${selectedDate === dateStr ? 'selected' : ''}`}
                onClick={() => setSelectedDate(dateStr)}
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
            <button type="button" className="btn btn-sm" onClick={() => onEditDay(selectedDate)}>
              {selectedInfo && selectedInfo.blocks.length > 0 ? 'Edit day' : 'Add working hours'}
            </button>
          </div>

          {selectedIsTimeOff && <p className="schedule-month-cell-timeoff standalone">Time off this day</p>}

          {selectedInfo && selectedInfo.blocks.length > 0 ? (
            <>
              <p className="muted" style={{ margin: 0 }}>
                Working hours ({selectedInfo.blocks.length} period{selectedInfo.blocks.length === 1 ? '' : 's'})
              </p>
              {selectedInfo.blocks.map((b) => (
                <div key={b.key} className="schedule-month-detail-period">
                  <span>
                    {formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}
                  </span>
                  <span className="pill">{departmentName(b.departmentId)}</span>
                  {b.breaks.length > 0 && (
                    <span className="muted schedule-sidebar-note">
                      {b.breaks.length} break{b.breaks.length === 1 ? '' : 's'}
                    </span>
                  )}
                </div>
              ))}
            </>
          ) : (
            !selectedIsTimeOff && <p className="muted">No working hours set for this day.</p>
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
