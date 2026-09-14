import { CalendarBlank, Warning } from '@phosphor-icons/react'
import { formatDate, formatTimeOfDay } from '../format'
import { toMinutesSinceMidnight, type ScheduleConflictDetail } from './doctorSchedule'

// Shared "Schedule conflict detected" state -- reused verbatim by Add
// Schedule, Edit Schedule and Duplicate Schedule (all three inside
// ConfigureScheduleModal/DuplicateScheduleModal) whenever
// checkScheduleConflicts finds an overlap. Deliberately not a second
// scheduling engine: every number and time range shown here comes
// straight from the ScheduleConflictDetail list the caller already
// computed with checkScheduleConflicts -- this component only lays it
// out. Distinguishing schedule-overlap conflicts (shown here) from
// pre-existing-appointment conflicts (a separate, already-established
// warning pattern) is the caller's job -- this panel never mentions
// appointments itself except via the optional `appointmentCount` prop.
function timeBar(startTime: string, endTime: string, dayStart: number, dayEnd: number): { left: string; width: string } {
  const span = Math.max(1, dayEnd - dayStart)
  const left = ((toMinutesSinceMidnight(startTime) - dayStart) / span) * 100
  const width = ((toMinutesSinceMidnight(endTime) - toMinutesSinceMidnight(startTime)) / span) * 100
  return { left: `${Math.max(0, left)}%`, width: `${Math.max(1, width)}%` }
}

function ConflictTimeline({ detail }: { detail: ScheduleConflictDetail }) {
  const dayStart = Math.min(
    toMinutesSinceMidnight(detail.existingStart),
    toMinutesSinceMidnight(detail.newStart),
  ) - 60
  const dayEnd = Math.max(
    toMinutesSinceMidnight(detail.existingEnd),
    toMinutesSinceMidnight(detail.newEnd),
  ) + 60
  return (
    <div className="conflict-timeline">
      <div className="conflict-timeline-row">
        <span className="conflict-timeline-label existing">Existing</span>
        <div className="conflict-timeline-track">
          <div className="conflict-timeline-bar existing" style={timeBar(detail.existingStart, detail.existingEnd, dayStart, dayEnd)}>
            {formatTimeOfDay(detail.existingStart)} – {formatTimeOfDay(detail.existingEnd)}
          </div>
        </div>
      </div>
      <div className="conflict-timeline-row">
        <span className="conflict-timeline-label new">New</span>
        <div className="conflict-timeline-track">
          <div className="conflict-timeline-bar new" style={timeBar(detail.newStart, detail.newEnd, dayStart, dayEnd)}>
            {formatTimeOfDay(detail.newStart)} – {formatTimeOfDay(detail.newEnd)}
          </div>
        </div>
      </div>
      <div className="conflict-timeline-row">
        <span className="conflict-timeline-label overlap">Conflict</span>
        <div className="conflict-timeline-track">
          <div className="conflict-timeline-bar overlap" style={timeBar(detail.overlapStart, detail.overlapEnd, dayStart, dayEnd)}>
            {formatTimeOfDay(detail.overlapStart)} – {formatTimeOfDay(detail.overlapEnd)}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function ScheduleConflictPanel({
  conflicts,
  totalDates,
  conflictDateCount,
  appointmentCount,
  newSummary,
  footer,
}: {
  conflicts: ScheduleConflictDetail[]
  totalDates: number | null
  conflictDateCount: number
  appointmentCount?: number
  newSummary?: { icon?: typeof CalendarBlank; label: string; lines: string[] }
  footer: React.ReactNode
}) {
  // One card per distinct date (bounded scope), or one card per distinct
  // conflict (unbounded scope, where `date` is null and there's no finite
  // list to group by) -- capped so a large multi-date scope doesn't dump
  // every conflicting date on screen at once.
  const grouped = new Map<string, ScheduleConflictDetail[]>()
  conflicts.forEach((c, i) => {
    const key = c.date ?? `unbounded-${i}`
    const list = grouped.get(key)
    if (list) list.push(c)
    else grouped.set(key, [c])
  })
  const groupEntries = Array.from(grouped.entries())
  const visible = groupEntries.slice(0, 6)
  const hiddenCount = groupEntries.length - visible.length

  return (
    <div className="schedule-modal-body schedule-conflict-panel">
      <div className="schedule-conflict-header">
        <span className="schedule-conflict-header-icon" aria-hidden="true">
          <Warning size={22} weight="fill" />
        </span>
        <div>
          <h4 className="schedule-conflict-title">Schedule conflict detected</h4>
          <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>
            This schedule overlaps with an existing schedule.
          </p>
        </div>
      </div>

      <div className="schedule-conflict-banner">
        <Warning size={18} weight="fill" />
        <span>
          <strong>{groupEntries.length} conflict{groupEntries.length === 1 ? '' : 's'} found</strong>
          <br />
          This schedule cannot be saved as configured because it overlaps with {groupEntries.length === 1 ? 'an existing schedule' : 'existing schedules'}.
        </span>
      </div>

      <div className="schedule-conflict-body">
        <div className="schedule-conflict-details">
          <span className="field-label">Conflict Details</span>
          <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
            Review the conflicting dates and time ranges.
          </p>
          {visible.map(([key, details]) => (
            <div key={key} className="schedule-conflict-detail-card">
              <div className="schedule-conflict-detail-date">
                {details[0].date ? formatDate(details[0].date) : 'Recurring, no end date'}
              </div>
              {details.map((d, i) => (
                <div key={i} className="schedule-conflict-detail-row">
                  <div>
                    <span className="field-label">Existing schedule</span>
                    <span>{formatTimeOfDay(d.existingStart)} – {formatTimeOfDay(d.existingEnd)}</span>
                  </div>
                  <div>
                    <span className="field-label">New schedule</span>
                    <span>{formatTimeOfDay(d.newStart)} – {formatTimeOfDay(d.newEnd)}</span>
                  </div>
                  <div className="schedule-conflict-overlap-time">
                    <span className="field-label">Overlapping time</span>
                    <span>{formatTimeOfDay(d.overlapStart)} – {formatTimeOfDay(d.overlapEnd)}</span>
                  </div>
                </div>
              ))}
              <ConflictTimeline detail={details[0]} />
            </div>
          ))}
          {hiddenCount > 0 && (
            <p className="muted schedule-sidebar-note">+{hiddenCount} more conflicting date{hiddenCount === 1 ? '' : 's'} not shown.</p>
          )}
        </div>

        <div className="schedule-conflict-side">
          {newSummary && (
            <div className="schedule-summary-card">
              {newSummary.icon ? <newSummary.icon size={18} /> : <CalendarBlank size={18} />}
              <div className="schedule-summary-card-body">
                <span className="schedule-summary-card-label">{newSummary.label}</span>
                <span className="schedule-summary-card-value">
                  {newSummary.lines.map((line, i) => <div key={i}>{line}</div>)}
                </span>
              </div>
            </div>
          )}

          {totalDates !== null && (
            <div className="schedule-conflict-stats">
              <span className="field-label">Schedule impact</span>
              <div className="schedule-conflict-stats-row">
                <div className="schedule-conflict-stat">
                  <span className="schedule-conflict-stat-value">{totalDates}</span>
                  <span className="schedule-conflict-stat-label">dates selected</span>
                </div>
                <div className="schedule-conflict-stat">
                  <span className="schedule-conflict-stat-value ok">{totalDates - conflictDateCount}</span>
                  <span className="schedule-conflict-stat-label">can be scheduled</span>
                </div>
                <div className="schedule-conflict-stat">
                  <span className="schedule-conflict-stat-value danger">{conflictDateCount}</span>
                  <span className="schedule-conflict-stat-label">have conflicts</span>
                </div>
              </div>
              <p className="muted schedule-sidebar-note" style={{ margin: '8px 0 0' }}>
                Only the dates without conflicts can be created after you adjust the schedule.
              </p>
            </div>
          )}

          {!!appointmentCount && appointmentCount > 0 && (
            <p className="schedule-scope-summary warning">
              <Warning size={16} weight="fill" />
              <span>
                <strong>Existing appointments</strong>
                <br />
                There {appointmentCount === 1 ? 'is' : 'are'} {appointmentCount} appointment{appointmentCount === 1 ? '' : 's'} on the conflicting dates.
                They will not be cancelled or moved.
              </span>
            </p>
          )}
        </div>
      </div>

      {footer}
    </div>
  )
}
