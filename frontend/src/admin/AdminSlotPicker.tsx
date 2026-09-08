import { useEffect, useState } from 'react'
import { ApiError, getSlotsForDate } from '../api'
import type { Slot } from '../types'
import AvailabilityBadge from '../AvailabilityBadge'
import SlotGrid from '../SlotGrid'
import AdminCalendar from './AdminCalendar'
import { formatAvailability, formatDate, formatTime } from '../format'

// Replaces the old free-form <input type="datetime-local"> this panel
// used for both "book on behalf of a patient" and "reschedule" -- that
// input let staff pick literally any minute of any day with no relation
// to the doctor's actual schedule, appointment duration, or existing
// bookings (and its native browser minute-scroll widget is what read as
// "rotating slots"). Date selection is now AdminCalendar -- a colour-
// coded month grid (teal = has open slots, grey = fully booked/no
// schedule that day) computed by GET /appointments/calendar, so staff
// see which days are worth clicking before they click one, rather than
// picking blind. Once a date is picked, the slot list below it is the
// exact same shared availability_engine every other booking path in
// this app already uses (via GET/POST /api/availability --
// unauthenticated, and deliberately NOT booking-window-limited,
// matching staff/admin bookings' existing exemption from the
// patient-facing 3-month window).
export default function AdminSlotPicker({
  doctorId,
  appointmentTypeId,
  durationMinutes,
  selectedSlot,
  onSelect,
  initialDate,
}: {
  doctorId: number
  appointmentTypeId: number
  durationMinutes: number
  selectedSlot: Slot | null
  onSelect: (slot: Slot) => void
  initialDate?: string
}) {
  const [date, setDate] = useState(initialDate ?? '')
  const [slots, setSlots] = useState<Slot[]>([])
  // That day's fixed capacity (see get_available_slots's count_total
  // docstring) -- feeds formatAvailability's fullness ratio below, the
  // same red/orange/green rule the patient-facing booking flows use
  // (format.ts), reusing the total_slots the shared /availability
  // endpoint already returns rather than a second calculation.
  const [totalSlots, setTotalSlots] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!date) {
      setSlots([])
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    getSlotsForDate(doctorId, appointmentTypeId, date)
      .then((result) => {
        if (!cancelled) {
          setSlots(result.slots)
          setTotalSlots(result.total_slots)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load time slots')
          setSlots([])
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [doctorId, appointmentTypeId, date])

  return (
    <div className="admin-slot-picker">
      <div className="slot-picker-context">
        <span className="pill duration-pill">{durationMinutes} min appointment</span>
        {date && !loading && (
          selectedSlot ? (
            <span className="muted">
              Selected {formatTime(selectedSlot.start_at)} on {formatDate(date)}
            </span>
          ) : (
            <AvailabilityBadge availability={formatAvailability(slots.length, totalSlots)} />
          )
        )}
      </div>

      <div className="admin-slot-picker-body">
        <AdminCalendar
          doctorId={doctorId}
          appointmentTypeId={appointmentTypeId}
          onSelectDate={setDate}
          selectedDate={date || null}
        />

        <div className="admin-slot-picker-slots">
          {error && <p className="error">{error}</p>}
          {date ? (
            <SlotGrid
              slots={slots}
              selectedSlot={selectedSlot}
              onSelect={onSelect}
              loading={loading}
              emptyMessage="The doctor has no open slots on this date -- try another date."
            />
          ) : (
            <p className="muted">Pick an available date on the calendar to see its time slots.</p>
          )}
        </div>
      </div>
    </div>
  )
}
