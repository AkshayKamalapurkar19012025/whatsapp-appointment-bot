import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
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
  const popoverRef = useRef<HTMLDivElement>(null)
  // The specific marker <span> currently open, if any -- set wherever a
  // marker opens itself below, read by the focusin dismissal handler.
  const openMarkerElRef = useRef<HTMLElement | null>(null)
  // Horizontal correction (px) applied on top of the popover's default
  // centered-on-the-marker position, so it stays on-screen for a
  // marked date in the leftmost/rightmost calendar column -- without
  // this, a plain centered popover runs off the viewport edge on a
  // narrow (mobile) screen since it's wider than a single day cell.
  const [popoverShift, setPopoverShift] = useState(0)
  // Flips the popover to render below its marker instead of above,
  // for a marked date in the calendar's first row where there isn't
  // room above it (e.g. scrolled near the top of the page) -- same
  // reasoning as popoverShift, just the vertical axis.
  const [popoverBelow, setPopoverBelow] = useState(false)

  useLayoutEffect(() => {
    if (!openMarkerDate) {
      setPopoverShift(0)
      setPopoverBelow(false)
      return
    }
    const el = popoverRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const margin = 8
    let shift = 0
    if (rect.left < margin) {
      shift = margin - rect.left
    } else if (rect.right > window.innerWidth - margin) {
      shift = window.innerWidth - margin - rect.right
    }
    setPopoverShift(shift)
    setPopoverBelow(rect.top < margin)
  }, [openMarkerDate])

  // Dismiss on Escape, and when keyboard focus moves anywhere other
  // than the marker that's currently open -- the pointer-based fix
  // above (onPointerEnter/onPointerLeave gated by pointerType, so a
  // touch tap's synthetic mouse events can't self-close the popover it
  // just opened) meant dropping the marker's old onBlur handler, which
  // otherwise closed it once a keyboard user tabbed off that specific
  // marker. This restores that dismissal via focus tracking instead
  // (checked against openMarkerElRef, set wherever a marker opens
  // itself below -- not just "left the grid", since Tab can move focus
  // to a different day cell/marker still inside the grid and this
  // popover should still close then), without reintroducing the touch
  // race: a touch tap focuses the marker itself, so this never fires
  // for the tap that opened it.
  useEffect(() => {
    if (!openMarkerDate) return
    function handleFocusOut(event: FocusEvent) {
      if (event.target !== openMarkerElRef.current) {
        setOpenMarkerDate(null)
      }
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpenMarkerDate(null)
    }
    document.addEventListener('focusin', handleFocusOut)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('focusin', handleFocusOut)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [openMarkerDate])

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
                    // Pointer Events, gated by pointerType, rather than
                    // onMouseEnter/onMouseLeave/onFocus/onBlur: a real
                    // touch tap synthesizes a full mouse-event burst
                    // afterwards (mouseenter, mousedown, mouseup, click,
                    // and critically a trailing mouseleave/blur as the
                    // "virtual pointer" leaves) -- the old mouse-based
                    // handlers opened it via mouseenter and then
                    // immediately closed it again via that trailing
                    // mouseleave, all within one gesture, faster than
                    // any render could show it. Only a real mouse's
                    // pointerType drives hover open/close now; onClick
                    // (fires for both mouse and touch) is what actually
                    // opens it on tap.
                    onPointerEnter={(event) => {
                      if (event.pointerType === 'mouse') {
                        openMarkerElRef.current = event.currentTarget
                        setOpenMarkerDate(cell.iso)
                      }
                    }}
                    onPointerLeave={(event) => {
                      if (event.pointerType === 'mouse') {
                        setOpenMarkerDate((current) => (current === cell.iso ? null : current))
                      }
                    }}
                    onClick={(event) => {
                      // Always open (not toggle): on a mouse, hover via
                      // onPointerEnter above already opened it, so this
                      // is a no-op re-open; on touch, this is the only
                      // thing that opens it. Dismissal is the
                      // outside-click handler above, or onPointerLeave
                      // for a real mouse.
                      event.stopPropagation()
                      openMarkerElRef.current = event.currentTarget
                      setOpenMarkerDate(cell.iso)
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        event.stopPropagation()
                        openMarkerElRef.current = event.currentTarget
                        setOpenMarkerDate((current) => (current === cell.iso ? null : cell.iso))
                      }
                    }}
                  >
                    {existingAppointments.length}
                  </span>
                )}
                {existingAppointments && existingAppointments.length > 0 && isPopoverOpen && (
                  <div
                    ref={popoverRef}
                    className={`calendar-day-popover${popoverBelow ? ' below' : ''}`}
                    role="tooltip"
                    style={{ '--popover-shift': `${popoverShift}px` } as CSSProperties}
                  >
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
