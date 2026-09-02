import { useEffect, useState } from 'react'
import { ApiError, getCalendarMonth } from './api'
import type { CalendarMonth } from './types'
import { isoDateOnly } from './format'

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

export default function Calendar({
  doctorId,
  appointmentTypeId,
  onSelectDate,
}: {
  doctorId: number
  appointmentTypeId: number
  onSelectDate: (isoDate: string) => void
}) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)
  const [data, setData] = useState<CalendarMonth | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getCalendarMonth(doctorId, appointmentTypeId, year, month)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load availability')
          setData(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [doctorId, appointmentTypeId, year, month])

  const isCurrentMonth = year === today.getFullYear() && month === today.getMonth() + 1
  const nextMonthDate = new Date(year, month, 1) // month is 1-based, so this rolls forward one
  const nextMonthKey = `${nextMonthDate.getFullYear()}-${String(nextMonthDate.getMonth() + 1).padStart(2, '0')}`
  const windowEndKey = data ? data.booking_window_end.slice(0, 7) : null
  const nextDisabled = windowEndKey !== null && nextMonthKey > windowEndKey

  function goPrev() {
    if (isCurrentMonth) return
    const prevDate = new Date(year, month - 2, 1)
    setYear(prevDate.getFullYear())
    setMonth(prevDate.getMonth() + 1)
  }

  function goNext() {
    if (nextDisabled) return
    setYear(nextMonthDate.getFullYear())
    setMonth(nextMonthDate.getMonth() + 1)
  }

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
        <button type="button" onClick={goPrev} disabled={isCurrentMonth} aria-label="Previous month">
          ‹
        </button>
        <span>
          {MONTH_NAMES[month - 1]} {year}
        </span>
        <button type="button" onClick={goNext} disabled={nextDisabled} aria-label="Next month">
          ›
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p>Loading availability…</p>}

      {data && (
        <div className="calendar-grid">
          {WEEKDAY_LABELS.map((label) => (
            <div key={label} className="calendar-weekday">
              {label}
            </div>
          ))}
          {cells.map((cell, index) => {
            if (cell === null) return <div key={`blank-${index}`} className="calendar-day empty" />
            const available = data.dates[cell.iso] === true
            return (
              <button
                key={cell.iso}
                type="button"
                className={`calendar-day${available ? ' available' : ' unavailable'}`}
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
