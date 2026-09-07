import { useEffect, useMemo, useState } from 'react'
import { ApiError, getCalendarMonth } from './api'
import type { CalendarMonth, MyAppointment } from './types'
import MonthGrid from './MonthGrid'
import { formatDate, formatTime } from './format'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './components/ui/alert-dialog'

export default function Calendar({
  doctorId,
  appointmentTypeId,
  departmentId,
  onSelectDate,
  doctorName,
  existingAppointments = [],
}: {
  doctorId: number
  appointmentTypeId: number
  // Narrows availability to this department's schedule (migrations/0010)
  // -- omit when there's no department in scope (e.g. rescheduling).
  departmentId?: number
  onSelectDate: (isoDate: string) => void
  // Doctor-first booking only (BookingFlow.tsx) -- the doctor's display
  // name for the "you already have an appointment with X" copy below,
  // and the patient's own upcoming appointments already booked with
  // this doctor, pre-filtered by the caller. Both omitted (defaulting
  // existingAppointments to []) wherever this component is reused
  // without that context (e.g. MyAppointments' reschedule calendar),
  // which simply renders no markers/nudge at all.
  doctorName?: string
  existingAppointments?: MyAppointment[]
}) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)
  const [data, setData] = useState<CalendarMonth | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // A date the patient just picked that already has an existing
  // appointment with this doctor -- holds the confirm-anyway dialog
  // below. null means no such prompt is showing.
  const [pendingDate, setPendingDate] = useState<string | null>(null)

  // start_at is the doctor-local wall-clock ISO string (see format.ts's
  // module docstring) -- its first 10 characters are exactly the
  // "YYYY-MM-DD" key MonthGrid's cells and getCalendarMonth's `dates`
  // both use, so no timezone conversion is needed here.
  const markedDates = useMemo(() => {
    const map: Record<string, MyAppointment[]> = {}
    for (const appointment of existingAppointments) {
      const iso = appointment.start_at.slice(0, 10)
      ;(map[iso] ??= []).push(appointment)
    }
    return map
  }, [existingAppointments])

  const monthKey = `${year}-${String(month).padStart(2, '0')}`
  const monthCount = existingAppointments.filter((a) => a.start_at.slice(0, 7) === monthKey).length

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

  // Doesn't block booking (a second, legitimate appointment with the
  // same doctor is a real use case -- follow-up, different concern) --
  // just nudges with a confirm step before forwarding to the real
  // onSelectDate for a date that already has one.
  function handleSelectDate(isoDate: string) {
    if (markedDates[isoDate]?.length) {
      setPendingDate(isoDate)
      return
    }
    onSelectDate(isoDate)
  }

  function confirmPendingDate() {
    if (pendingDate) onSelectDate(pendingDate)
    setPendingDate(null)
  }

  // The existing appointments on the pending date, earliest first --
  // used by the confirm dialog below to name a time, not just a date
  // (without it, "you already have an appointment on 9 Sep" gives no
  // way to tell whether that's the exact same slot, an overlapping
  // one, or just a different time earlier/later that day, which is
  // the one thing this dialog exists to help the patient decide).
  const pendingDateAppointments = pendingDate
    ? [...(markedDates[pendingDate] ?? [])].sort((a, b) => a.start_at.localeCompare(b.start_at))
    : []

  return (
    <>
      {doctorName && monthCount > 0 && (
        <p className="calendar-month-nudge">
          You have {monthCount} other {monthCount === 1 ? 'appointment' : 'appointments'} with {doctorName} this
          month.
        </p>
      )}
      <MonthGrid
        year={year}
        month={month}
        dates={data ? data.dates : null}
        loading={loading}
        error={error}
        onSelectDate={handleSelectDate}
        onPrevMonth={goPrev}
        onNextMonth={goNext}
        prevDisabled={isCurrentMonth}
        nextDisabled={nextDisabled}
        markedDates={markedDates}
        doctorName={doctorName}
      />

      <AlertDialog open={pendingDate !== null} onOpenChange={(open) => !open && setPendingDate(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingDateAppointments.length > 1
                ? 'You already have appointments this day'
                : 'You already have an appointment this day'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDate &&
                pendingDateAppointments.length > 0 &&
                (() => {
                  const times = pendingDateAppointments.map(
                    (a) => `${formatTime(a.start_at)}–${formatTime(a.end_at)}`,
                  )
                  // "A", "A and B", or "A, B, and C" -- every existing
                  // slot named, not just the earliest, so the patient
                  // can actually tell whether their new pick overlaps
                  // ANY of them, not only the first.
                  const timesList =
                    times.length === 1
                      ? times[0]
                      : times.length === 2
                        ? `${times[0]} and ${times[1]}`
                        : `${times.slice(0, -1).join(', ')}, and ${times[times.length - 1]}`

                  return pendingDateAppointments.length === 1
                    ? `You already have an appointment with ${doctorName ?? 'this doctor'} on ${formatDate(pendingDate)}, ` +
                        `${timesList}. Continue booking anyway?`
                    : `You have ${pendingDateAppointments.length} appointments with ${doctorName ?? 'this doctor'} on ` +
                        `${formatDate(pendingDate)}, at ${timesList}. Continue booking anyway?`
                })()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmPendingDate}>Continue anyway</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
