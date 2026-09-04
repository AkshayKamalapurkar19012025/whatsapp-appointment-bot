import { CalendarX } from '@phosphor-icons/react'
import type { Slot } from './types'
import { formatTime } from './format'

// The one clickable slot-time control every flow in the app uses --
// patient booking, patient reschedule, and (as of this change) admin
// create/reschedule too. Deliberately dumb/presentational: it only
// renders whatever slot list it's given: doctor-schedule-derived, with a
// clear Start -> End relationship per chip and grouped by time of day so
// a long list stays scannable, replacing every free-form time input this
// app used to have.
export default function SlotGrid({
  slots,
  selectedSlot,
  onSelect,
  loading,
  emptyMessage = 'No times are available on this date -- try another date.',
}: {
  slots: Slot[]
  selectedSlot?: Slot | null
  onSelect: (slot: Slot) => void
  loading?: boolean
  emptyMessage?: string
}) {
  if (loading) {
    return (
      <div className="state-block">
        <span className="spinner" aria-hidden="true" />
        Loading available times…
      </div>
    )
  }

  if (slots.length === 0) {
    return (
      <div className="state-block empty">
        <span className="state-icon" aria-hidden="true">
          <CalendarX size={28} weight="light" />
        </span>
        {emptyMessage}
      </div>
    )
  }

  const groups: { label: string; slots: Slot[] }[] = [
    { label: 'Morning', slots: [] },
    { label: 'Afternoon', slots: [] },
    { label: 'Evening', slots: [] },
  ]
  for (const slot of slots) {
    const hour = Number(slot.start_at.match(/T(\d{2}):/)?.[1] ?? '0')
    if (hour < 12) groups[0].slots.push(slot)
    else if (hour < 17) groups[1].slots.push(slot)
    else groups[2].slots.push(slot)
  }

  return (
    <div className="slot-groups">
      {groups
        .filter((g) => g.slots.length > 0)
        .map((group) => (
          <div key={group.label} className="slot-group">
            <div className="slot-group-label">{group.label}</div>
            <div className="slot-grid">
              {group.slots.map((slot) => (
                <button
                  key={slot.start_at}
                  type="button"
                  className={`slot-chip${selectedSlot?.start_at === slot.start_at ? ' selected' : ''}`}
                  onClick={() => onSelect(slot)}
                  title={`${formatTime(slot.start_at)} – ${formatTime(slot.end_at)}`}
                >
                  {formatTime(slot.start_at)}
                </button>
              ))}
            </div>
          </div>
        ))}
    </div>
  )
}
