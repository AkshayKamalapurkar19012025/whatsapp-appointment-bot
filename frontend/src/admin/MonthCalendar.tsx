import { useRef, useState, type ReactNode } from 'react'
import { CalendarBlank } from '@phosphor-icons/react'
import { usePreviewPopover } from '../usePreviewPopover'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

// Monday-first column (0..6) for the 1st of the month.
export function firstWeekdayColumn(year: number, month: number): number {
  const jsDay = new Date(year, month - 1, 1).getDay()
  return (jsDay + 6) % 7
}

export function isoDate(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

// Shared monthly calendar shell used by both the Schedule and Time off
// tabs (ScheduleMonthView.tsx / TimeOffSection.tsx) -- prev/next month,
// the calendar-icon month/year jump popover, a "Today" pulse, the
// Working/Partial/Time off/No schedule legend, and the 7-column day
// grid. It owns navigation state and grid layout only; each caller
// supplies the legend's "partial" wording and renders its own cell
// content and toolbar extras (department filter, admin actions), since
// those differ between the two tabs' underlying data and actions.
export default function MonthCalendar({
  legendPartialLabel = 'Partial day',
  filterSlot,
  toolbarExtra,
  onDateClick,
  renderCellContent,
}: {
  legendPartialLabel?: string
  filterSlot?: ReactNode
  toolbarExtra?: ReactNode
  onDateClick: (dateStr: string) => void
  renderCellContent: (dateStr: string, day: number) => ReactNode
}) {
  const today = new Date()
  const todayIso = isoDate(today.getFullYear(), today.getMonth() + 1, today.getDate())
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)
  // Dismisses on an outside click or Escape, not just by clicking the
  // calendar icon again -- see ScheduleMonthView.tsx's own Slot
  // settings popover for the same fix and why (usePreviewPopover).
  const { open: jumpOpen, setOpen: setJumpOpen, containerRef: jumpRef } = usePreviewPopover<HTMLDivElement>()
  // Briefly rings today's cell after "Today" is clicked, so jumping back
  // to the current month makes it immediately obvious which date that is.
  const [highlightedDate, setHighlightedDate] = useState<string | null>(null)
  const highlightTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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
      {filterSlot && <div className="schedule-month-filters">{filterSlot}</div>}

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
        <div className="schedule-month-jump" ref={jumpRef}>
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

        {toolbarExtra}
      </div>

      <div className="schedule-month-legend">
        <span className="schedule-legend-dot working" /> Working day
        <span className="schedule-legend-dot partial" /> {legendPartialLabel}
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
          return (
            <button
              key={i}
              type="button"
              className={`schedule-month-cell${dateStr === highlightedDate ? ' highlighted' : ''}`}
              onClick={() => onDateClick(dateStr)}
            >
              <span className="schedule-month-cell-date">{day}</span>
              {renderCellContent(dateStr, day)}
            </button>
          )
        })}
      </div>
    </div>
  )
}
