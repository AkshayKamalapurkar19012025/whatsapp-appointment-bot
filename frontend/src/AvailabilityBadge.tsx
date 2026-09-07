import type { Availability } from './format'

// One shared rendering for format.ts's formatAvailability() output --
// Date-First's doctor cards (DoctorCard's `extra` slot) and Doctor-
// First's Slot-step banner both use this, so the color/text rule
// never has two independent implementations.
export default function AvailabilityBadge({ availability }: { availability: Availability }) {
  return (
    <span className={`availability-block availability-${availability.level}`}>
      <span className="availability-main">
        <span className="availability-dot" aria-hidden="true" />
        {availability.label}
      </span>
      {availability.sub && <span className="availability-sub">{availability.sub}</span>}
    </span>
  )
}
