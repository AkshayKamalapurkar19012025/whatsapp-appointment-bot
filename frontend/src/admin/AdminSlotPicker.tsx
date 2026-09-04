import { useEffect, useState } from 'react'
import { ApiError, getSlotsForDate } from '../api'
import type { Slot } from '../types'
import SlotGrid from '../SlotGrid'
import { formatDate } from '../format'

// Replaces the old free-form <input type="datetime-local"> this panel
// used for both "book on behalf of a patient" and "reschedule" -- that
// input let staff pick literally any minute of any day with no relation
// to the doctor's actual schedule, appointment duration, or existing
// bookings (and its native browser minute-scroll widget is what read as
// "rotating slots"). This asks for a date, then shows only the real,
// currently-open slots for that doctor/appointment type, computed by the
// exact same shared availability_engine every other booking path in this
// app already uses (via GET/POST /api/availability -- unauthenticated,
// and deliberately NOT booking-window-limited, matching staff/admin
// bookings' existing exemption from the patient-facing 3-month window).
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
        if (!cancelled) setSlots(result.slots)
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

  const today = new Date().toISOString().slice(0, 10)

  return (
    <div className="admin-slot-picker">
      <div className="slot-picker-context">
        <span className="pill duration-pill">{durationMinutes} min appointment</span>
        {date && (
          <span className="muted">
            {slots.length} slot{slots.length === 1 ? '' : 's'} open on {formatDate(date)}
          </span>
        )}
      </div>

      <label className="field">
        <span className="field-label">Date</span>
        <input
          type="date"
          value={date}
          min={today}
          onChange={(e) => {
            setDate(e.target.value)
          }}
          required
        />
      </label>

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
        <p className="muted">Pick a date to see the doctor's open slots.</p>
      )}
    </div>
  )
}
