import { isoDateOnly } from './format'
import { useClinicToday } from './useClinicToday'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

// Monday-first column index (0..6) for the 1st of the month, matching
// day_of_week's Monday=1 convention used throughout the backend.
function firstWeekdayColumn(year: number, month: number): number {
  const jsDay = new Date(year, month - 1, 1).getDay() // Sunday = 0
  return (jsDay + 6) % 7
}

// The presentational month-grid every calendar picker in this app
// renders -- extracted from what used to be Calendar.tsx's own JSX so
// the patient booking calendar (window-limited, via GET /web/calendar)
// and the admin date picker (unrestricted, via GET /appointments/
// calendar) can share the exact same grid/legend/nav-button rendering
// while each keeps its own data-fetching and window rules, which
// genuinely differ between the two callers.
export default function MonthGrid({
  year,
  month,
  dates,
  loading,
  error,
  onSelectDate,
  onPrevMonth,
  onNextMonth,
  prevDisabled,
  nextDisabled,
  showLegend = true,
  selectedDate = null,
}: {
  year: number
  month: number
  dates: Record<string, boolean> | null
  loading?: boolean
  error?: string | null
  onSelectDate: (isoDate: string) => void
  onPrevMonth: () => void
  onNextMonth: () => void
  prevDisabled: boolean
  nextDisabled: boolean
  // The "Unavailable/Available" legend below only makes sense when
  // `dates` reflects real appointment-slot availability (the patient/
  // admin booking calendars). AdminDatePicker reuses this same grid for
  // plain administrative dates (schedule start/end, one-off block
  // dates) where greyed-out just means "in the past" -- showing the
  // availability legend there would misleadingly imply doctor
  // availability that was never computed.
  showLegend?: boolean
  // The currently-chosen date (if any), highlighted distinctly from a
  // merely-available one. Only meaningful for a caller whose calendar
  // and its downstream slot picker stay visible together after picking
  // a date (AdminSlotPicker.tsx) -- the patient booking flow advances
  // past the calendar entirely on selection, so it never has a
  // "selected but still looking at the calendar" state to show and
  // simply omits this prop.
  selectedDate?: string | null
}) {
  // The clinic's own current date (not the viewer's device date) --
  // display-only, for the "Today" ring below; never the source of
  // truth for which dates are actually bookable (that's `dates`
  // itself, computed server-side per doctor's own timezone).
  const today = useClinicToday()
  const totalDays = daysInMonth(year, month)
  const leadingBlanks = firstWeekdayColumn(year, month)
  const cells: Array<{ day: number; iso: string } | null> = []
  for (let i = 0; i < leadingBlanks; i++) cells.push(null)
  for (let day = 1; day <= totalDays; day++) {
    cells.push({ day, iso: isoDateOnly(year, month, day) })
  }

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
        <div className="calendar-grid">
          {WEEKDAY_LABELS.map((label) => (
            <div key={label} className="calendar-weekday">
              {label}
            </div>
          ))}
          {cells.map((cell, index) => {
            if (cell === null) return <div key={`blank-${index}`} className="calendar-day empty" />
            const available = dates[cell.iso] === true
            const isSelected = selectedDate === cell.iso
            const isToday = today === cell.iso
            const classNames = [
              'calendar-day',
              available ? 'available' : 'unavailable',
              isSelected && 'selected',
              isToday && 'today',
            ]
              .filter(Boolean)
              .join(' ')
            return (
              <button
                key={cell.iso}
                type="button"
                className={classNames}
                disabled={!available}
                aria-current={isToday ? 'date' : undefined}
                aria-pressed={isSelected}
                title={isToday ? 'Today' : undefined}
                onClick={() => onSelectDate(cell.iso)}
              >
                {cell.day}
              </button>
            )
          })}
        </div>
      )}

      {dates && !loading && showLegend && Object.values(dates).length > 0 && (
        Object.values(dates).every((available) => !available) && (
          <p className="calendar-empty-state">
            No available dates this month. Try another month.
          </p>
        )
      )}

      {showLegend && (
        <p className="calendar-legend">
          <span className="legend-swatch unavailable" /> Unavailable
          <span className="legend-swatch available" /> Available
          {selectedDate !== null && (
            <>
              <span className="legend-swatch selected" /> Selected
            </>
          )}
          <span className="legend-swatch today" /> Today
        </p>
      )}
    </div>
  )
}
