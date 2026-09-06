import { useEffect, useRef, useState } from 'react'
import { formatTime, isoDateOnly } from './format'
import { useClinicToday } from './useClinicToday'
import type { MyAppointment } from './types'

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
  markedDates,
  doctorName,
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
  // Doctor-first booking only (via Calendar.tsx) -- dates the patient
  // already has an upcoming appointment with this doctor on, keyed by
  // "YYYY-MM-DD". Undefined/empty everywhere else this grid is reused
  // (admin picker, reschedule calendar), which simply renders no
  // markers. doctorName is just display copy for the popover text.
  markedDates?: Record<string, MyAppointment[]>
  doctorName?: string
}) {
  // Which marked date's popover is currently open (hover on desktop,
  // tap on mobile) -- at most one at a time.
  const [openMarkerDate, setOpenMarkerDate] = useState<string | null>(null)
  const gridRef = useRef<HTMLDivElement>(null)

  // Dismiss an open popover on an outside tap/click -- the only close
  // path on touch devices, which don't fire mouseleave. Not a toggle on
  // the marker's own click (see below): a real click/tap always fires a
  // synthetic mouseenter first, which would open-then-immediately-close
  // it in the same gesture if the click handler toggled instead of just
  // opening.
  useEffect(() => {
    if (!openMarkerDate) return
    function handleOutside(event: MouseEvent) {
      if (gridRef.current && !gridRef.current.contains(event.target as Node)) {
        setOpenMarkerDate(null)
      }
    }
    document.addEventListener('click', handleOutside, true)
    return () => document.removeEventListener('click', handleOutside, true)
  }, [openMarkerDate])
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
        <div className="calendar-grid" ref={gridRef}>
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
            const existingAppointments = markedDates?.[cell.iso]
            const classNames = [
              'calendar-day',
              available ? 'available' : 'unavailable',
              isSelected && 'selected',
              isToday && 'today',
            ]
              .filter(Boolean)
              .join(' ')
            const isPopoverOpen = openMarkerDate === cell.iso
            return (
              <div key={cell.iso} className="calendar-day-cell">
                <button
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
                {existingAppointments && existingAppointments.length > 0 && (
                  <span
                    className="calendar-day-marker"
                    role="button"
                    tabIndex={0}
                    aria-label={`You already have ${existingAppointments.length === 1 ? 'an appointment' : `${existingAppointments.length} appointments`} with ${doctorName ?? 'this doctor'} on this date -- show details`}
                    aria-expanded={isPopoverOpen}
                    onMouseEnter={() => setOpenMarkerDate(cell.iso)}
                    onMouseLeave={() => setOpenMarkerDate((current) => (current === cell.iso ? null : current))}
                    onFocus={() => setOpenMarkerDate(cell.iso)}
                    onBlur={() => setOpenMarkerDate((current) => (current === cell.iso ? null : current))}
                    onClick={(event) => {
                      // Always open (not toggle): a tap/click also fires
                      // a synthetic mouseenter first, which already
                      // opened this same popover -- toggling here would
                      // immediately close what the tap just opened.
                      // Dismissal is the outside-click handler above (or
                      // mouseleave/blur on desktop).
                      event.stopPropagation()
                      setOpenMarkerDate(cell.iso)
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        event.stopPropagation()
                        setOpenMarkerDate((current) => (current === cell.iso ? null : cell.iso))
                      }
                    }}
                  >
                    {existingAppointments.length}
                  </span>
                )}
                {existingAppointments && existingAppointments.length > 0 && isPopoverOpen && (
                  <div className="calendar-day-popover" role="tooltip">
                    <p className="calendar-day-popover-title">
                      {MONTH_NAMES[Number(cell.iso.slice(5, 7)) - 1]} {cell.day} with {doctorName ?? 'this doctor'}
                    </p>
                    {existingAppointments.map((appointment) => (
                      <div key={appointment.id} className="calendar-day-popover-row">
                        <span className="calendar-day-popover-time">
                          {formatTime(appointment.start_at)}–{formatTime(appointment.end_at)}
                        </span>
                        <span className={`pill status-${appointment.status.toLowerCase()}`}>
                          {appointment.status}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
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
