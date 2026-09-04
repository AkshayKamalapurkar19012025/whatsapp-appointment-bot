import { isoDateOnly } from './format'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

// The presentational date-strip every calendar picker in this app
// renders -- extracted from what used to be Calendar.tsx's own JSX so
// the patient booking calendar (window-limited, via GET /web/calendar)
// and the admin date picker (unrestricted, via GET /appointments/
// calendar) can share the exact same strip/legend/nav-button rendering
// while each keeps its own data-fetching and window rules, which
// genuinely differ between the two callers.
//
// Renders as a wrapping row of round day-number buttons (not a
// weekday-aligned 7-column grid) -- deliberately NOT a horizontally
// scrolling strip: ui-ux-pro-max's own `horizontal-scroll` rule flags
// horizontal scroll as an anti-pattern, so this wraps onto multiple rows
// instead, keeping the same "row of day circles" look without it.
export default function MonthGrid({
  year,
  month,
  dates,
  loading,
  error,
  selectedDate,
  onSelectDate,
  onPrevMonth,
  onNextMonth,
  prevDisabled,
  nextDisabled,
}: {
  year: number
  month: number
  dates: Record<string, boolean> | null
  loading?: boolean
  error?: string | null
  // Optional: highlights the currently-picked day. Only meaningful for a
  // caller that keeps this calendar visible after a date is chosen (see
  // AdminCalendar.tsx) -- callers that immediately advance to the next
  // step once a date is picked (Calendar.tsx's patient/reschedule flows)
  // never show this component again after selection, so they don't pass
  // it.
  selectedDate?: string | null
  onSelectDate: (isoDate: string) => void
  onPrevMonth: () => void
  onNextMonth: () => void
  prevDisabled: boolean
  nextDisabled: boolean
}) {
  const totalDays = daysInMonth(year, month)
  const days = Array.from({ length: totalDays }, (_, i) => {
    const day = i + 1
    return { day, iso: isoDateOnly(year, month, day) }
  })

  return (
    <div className="calendar">
      <div className="calendar-header">
        <button type="button" onClick={onPrevMonth} disabled={prevDisabled} aria-label="Previous month">
          ‹
        </button>
        <span>
          {MONTH_NAMES[month - 1]} {year}
        </span>
        <button type="button" onClick={onNextMonth} disabled={nextDisabled} aria-label="Next month">
          ›
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading availability…
        </div>
      )}

      {dates && !loading && (
        <div className="date-strip">
          {days.map((cell) => {
            const available = dates[cell.iso] === true
            const selected = cell.iso === selectedDate
            return (
              <button
                key={cell.iso}
                type="button"
                className={`date-strip-day${available ? ' available' : ' unavailable'}${selected ? ' selected' : ''}`}
                disabled={!available}
                onClick={() => onSelectDate(cell.iso)}
              >
                {cell.day}
              </button>
            )
          })}
        </div>
      )}

      <p className="calendar-legend">
        <span className="legend-swatch unavailable" /> Unavailable
        <span className="legend-swatch available" /> Available
      </p>
    </div>
  )
}
