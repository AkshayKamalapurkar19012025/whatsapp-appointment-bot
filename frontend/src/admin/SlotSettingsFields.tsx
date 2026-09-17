// The doctor-level "Buffer between appointments" field -- shared by
// ScheduleMonthView's toolbar popover and Configure Schedule Step 3's
// "Change slot settings" link, so there is exactly one place this field
// (and its explanatory copy) is defined, even though it's opened from
// two different spots in the UI.
//
// There used to be a second, editable "Calendar preview grid" duration
// here too -- a doctor-level default_duration_minutes with no
// connection to any real appointment type, so the Working-Hours Preview
// it drove could (and did) show slot boundaries that didn't match any
// actually-bookable start time. Removed: the preview now always uses a
// real appointment type's own duration (previewDurationLabel says
// which one), so there is nothing left here to independently edit.
export default function SlotSettingsFields({
  bufferMinutes,
  previewDurationLabel,
  onChangeBuffer,
}: {
  bufferMinutes: number
  // e.g. "30 minutes (Consultation)", or null if this doctor has no
  // appointment types assigned yet -- see ScheduleGrid.tsx's own
  // previewDurationMinutes/previewDurationLabel computation.
  previewDurationLabel: string | null
  onChangeBuffer: (minutes: number) => void
}) {
  return (
    <>
      <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
        Doctor-level setting -- applies to every schedule for this doctor, not just one date.
      </p>
      <p className="muted schedule-sidebar-note">
        {previewDurationLabel
          ? `Preview grid: ${previewDurationLabel} -- the doctor's own real appointment length, so preview slot times match what's actually bookable.`
          : 'Preview grid: assign an appointment type to this doctor (Appointment Types tab) to see real slot times here.'}
      </p>
      <label className="inline-label">
        Buffer between appointments
        <select value={bufferMinutes} onChange={(e) => onChangeBuffer(Number(e.target.value))}>
          {[0, 5, 10, 15, 20, 30].map((b) => (
            <option key={b} value={b}>
              {b === 0 ? 'No buffer' : `${b} minutes`}
            </option>
          ))}
        </select>
      </label>
      <p className="muted schedule-sidebar-note">Applied for real between generated appointment slots.</p>
    </>
  )
}
