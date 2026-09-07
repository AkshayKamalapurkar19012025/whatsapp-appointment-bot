import { useEffect, useState } from 'react'
import { ApiError, getDepartmentCalendarMonth } from './api'
import type { CalendarMonth } from './types'
import MonthGrid from './MonthGrid'

// Date-First's aggregate calendar: department + appointment type instead
// of a single doctor -- otherwise a straight copy of Calendar.tsx's own
// data-fetching/window-navigation logic, reusing the exact same
// MonthGrid presentational component. Kept as its own small component
// (not a doctorId?/departmentId? union bolted onto Calendar.tsx) since
// the two data sources (GET /web/calendar vs GET /web/calendar/
// department) are genuinely different endpoints with different
// aggregation semantics, not the same call with an optional parameter.
export default function DepartmentCalendar({
  departmentId,
  appointmentTypeId,
  onSelectDate,
}: {
  departmentId: number
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
    getDepartmentCalendarMonth(departmentId, appointmentTypeId, year, month)
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
  }, [departmentId, appointmentTypeId, year, month])

  const isCurrentMonth = year === today.getFullYear() && month === today.getMonth() + 1
  const nextMonthDate = new Date(year, month, 1)
  const nextMonthKey = `${nextMonthDate.getFullYear()}-${String(nextMonthDate.getMonth() + 1).padStart(2, '0')}`
  const windowEndKey = data ? data.scheduling_window_end.slice(0, 7) : null
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

  return (
    <MonthGrid
      year={year}
      month={month}
      dates={data ? data.dates : null}
      loading={loading}
      error={error}
      onSelectDate={onSelectDate}
      onPrevMonth={goPrev}
      onNextMonth={goNext}
      prevDisabled={isCurrentMonth}
      nextDisabled={nextDisabled}
    />
  )
}
