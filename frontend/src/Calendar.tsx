import { useEffect, useState } from 'react'
import { ApiError, getCalendarMonth } from './api'
import type { CalendarMonth } from './types'
import MonthGrid from './MonthGrid'

export default function Calendar({
  doctorId,
  appointmentTypeId,
  departmentId,
  onSelectDate,
}: {
  doctorId: number
  appointmentTypeId: number
  // Narrows availability to this department's schedule (migrations/0010)
  // -- omit when there's no department in scope (e.g. rescheduling).
  departmentId?: number
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
    getCalendarMonth(doctorId, appointmentTypeId, year, month, departmentId)
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
  }, [doctorId, appointmentTypeId, departmentId, year, month])

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
