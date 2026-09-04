import { useEffect, useState } from 'react'
import { ApiError, getAdminCalendarMonth } from '../api'
import MonthGrid from '../MonthGrid'

// The admin/staff equivalent of Calendar.tsx (patient booking), backed
// by GET /appointments/calendar instead of GET /web/calendar -- no
// booking-window limit, so "next month" is never disabled and a date
// far in the future still shows real open/full colour-coding instead of
// being blanked out or rejected. See AdminSlotPicker.tsx's docstring for
// why this replaced a plain <input type="date">.
export default function AdminCalendar({
  doctorId,
  appointmentTypeId,
  selectedDate,
  onSelectDate,
}: {
  doctorId: number
  appointmentTypeId: number
  selectedDate?: string | null
  onSelectDate: (isoDate: string) => void
}) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)
  const [dates, setDates] = useState<Record<string, boolean> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getAdminCalendarMonth(doctorId, appointmentTypeId, year, month)
      .then((result) => {
        if (!cancelled) setDates(result.dates)
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load availability')
          setDates(null)
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

  function goPrev() {
    if (isCurrentMonth) return
    const prevDate = new Date(year, month - 2, 1)
    setYear(prevDate.getFullYear())
    setMonth(prevDate.getMonth() + 1)
  }

  function goNext() {
    const nextDate = new Date(year, month, 1)
    setYear(nextDate.getFullYear())
    setMonth(nextDate.getMonth() + 1)
  }

  return (
    <MonthGrid
      year={year}
      month={month}
      dates={dates}
      loading={loading}
      error={error}
      selectedDate={selectedDate}
      onSelectDate={onSelectDate}
      onPrevMonth={goPrev}
      onNextMonth={goNext}
      prevDisabled={isCurrentMonth}
      nextDisabled={false}
    />
  )
}
