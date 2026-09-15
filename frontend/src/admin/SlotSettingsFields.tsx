import { DURATION_OPTIONS } from './doctorSchedule'

// The doctor-level "Preview interval"/"Buffer between appointments"
// fields -- shared by ScheduleMonthView's toolbar popover and Configure
// Schedule Step 3's "Change slot settings" link, so there is exactly
// one place these two fields (and their explanatory copy) are defined,
// even though they're opened from two different spots in the UI.
export default function SlotSettingsFields({
  defaultDuration,
  bufferMinutes,
  onChangeDuration,
  onChangeBuffer,
}: {
  defaultDuration: number
  bufferMinutes: number
  onChangeDuration: (minutes: number) => void
  onChangeBuffer: (minutes: number) => void
}) {
  return (
    <>
      <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
        Doctor-level settings -- apply to every schedule for this doctor, not just one date.
      </p>
      <p className="muted schedule-sidebar-note">
        Grid size for this preview only -- actual appointment lengths are set per appointment type
        (Appointment Types tab) and are unaffected by this setting.
      </p>
      <label className="inline-label">
        Calendar preview grid
        <select value={defaultDuration} onChange={(e) => onChangeDuration(Number(e.target.value))}>
          {DURATION_OPTIONS.map((d) => (
            <option key={d} value={d}>
              {d} minutes
            </option>
          ))}
        </select>
      </label>
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
