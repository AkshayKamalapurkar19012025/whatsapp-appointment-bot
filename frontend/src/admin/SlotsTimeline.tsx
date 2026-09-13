import { formatTimeOfDay } from '../format'
import { countSlots, type TimelineSegment } from './doctorSchedule'

// Visual timeline for the Doctor Schedule "Generated Slots Preview" --
// replaces the old flat list of slot-start chips with one continuous
// read of the day's shape: bookable slots, breaks, and the gaps between
// working periods, in chronological order, with a connecting line down
// the left so the passage of time (and where it stops) is obvious at a
// glance. Pure presentation -- the underlying segments come from
// timelineForBlocks (doctorSchedule.ts), the same duration+buffer walk
// templateDaySlots itself uses, so what's drawn here always matches
// what would actually be generated.
//
// Explicitly a draft/template preview, not real booking availability --
// existing appointments and time off aren't excluded here (see the
// disclaimer this component renders below the summary).
export default function SlotsTimeline({ segments }: { segments: TimelineSegment[] }) {
  const { total, morning, afternoon } = countSlots(segments)

  if (segments.length === 0) {
    return <p className="muted schedule-sidebar-note">No working hours configured.</p>
  }
  if (total === 0) {
    return (
      <div className="slots-timeline-empty">
        <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>
          No appointment slots can be generated from this schedule.
        </p>
      </div>
    )
  }

  return (
    <>
      <div className="slots-timeline">
        {segments.map((seg, i) => (
          <div key={i} className="slots-timeline-row">
            <span className="slots-timeline-time">{formatTimeOfDay(seg.start)}</span>
            <div className={`slots-timeline-content ${seg.type}`}>
              <div className={`slots-timeline-chip ${seg.type}`}>
                <span>
                  {formatTimeOfDay(seg.start)} – {formatTimeOfDay(seg.end)}
                </span>
                <span className="slots-timeline-badge">
                  {seg.type === 'slot' ? 'Available' : seg.type === 'break' ? '☕ Break' : 'Not available'}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
      <p className="slots-timeline-summary">
        <span className="slots-timeline-summary-check" aria-hidden="true">
          ✓
        </span>
        {total === 1 ? '1 appointment slot available' : `${total} appointment slots available per day`}
        {total > 0 && (
          <span className="muted">
            {' '}
            &middot; {morning} morning &middot; {afternoon} afternoon
          </span>
        )}
      </p>
    </>
  )
}
