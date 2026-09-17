import { useState } from 'react'
import { Gear } from '@phosphor-icons/react'
import type { Department, DoctorBlockEntry } from '../types'
import { formatTimeOfDay } from '../format'
import { usePreviewPopover } from '../usePreviewPopover'
import { dayAvailability, type ScheduleBlock } from './doctorSchedule'
import MonthCalendar from './MonthCalendar'
import SlotSettingsFields from './SlotSettingsFields'

// Monthly calendar for the Schedule tab -- a pure VIEW of the doctor's
// schedule, built on the shared MonthCalendar shell (MonthCalendar.tsx)
// also used by the Time off tab. Every mutation (create a schedule, edit
// or remove an existing one) happens in the Configure Schedule popup
// (ScheduleGrid.tsx owns opening it, since it needs the full unfiltered
// block list to compute the edit target); this component only renders
// the department filter, admin toolbar actions, and each day cell's
// content, and reports which date was clicked or that "+ Add Schedule"
// was pressed. See ScheduleGrid.tsx's own top-of-file comment for the
// full workflow.
export default function ScheduleMonthView({
  blocks,
  oneOffBlocks,
  departments,
  defaultDuration,
  bufferMinutes,
  isAdmin,
  onDateClick,
  onAddSchedule,
  onChangeDuration,
  onChangeBuffer,
}: {
  blocks: ScheduleBlock[]
  oneOffBlocks: DoctorBlockEntry[]
  departments: Department[]
  defaultDuration: number
  bufferMinutes: number
  isAdmin: boolean
  onDateClick: (dateStr: string) => void
  onAddSchedule: () => void
  onChangeDuration: (minutes: number) => void
  onChangeBuffer: (minutes: number) => void
}) {
  // Dismisses on an outside click or Escape (usePreviewPopover, shared
  // with DepartmentsPanel/DoctorsPanel's own hover-preview popovers) --
  // previously a bare useState that only ever closed by clicking the
  // Slot settings button again, so it stayed open no matter where else
  // you clicked.
  const { open: slotSettingsOpen, setOpen: setSlotSettingsOpen, containerRef: slotSettingsRef } = usePreviewPopover<HTMLDivElement>()
  const [filterDepartmentId, setFilterDepartmentId] = useState('')

  // Department filter -- real (blocks are actually department_id-scoped
  // in doctor_schedule, migrations/0010), a view-only lens on the
  // calendar; doesn't affect what a date click edits. A block with no
  // department (departmentId === null) applies regardless of
  // department -- see doctorSchedule.ts's own note on this -- so it
  // must still show up under any specific department filter, not just
  // "All departments".
  const visibleBlocks = filterDepartmentId
    ? blocks.filter((b) => b.departmentId === null || b.departmentId === Number(filterDepartmentId))
    : blocks

  return (
    <MonthCalendar
      legendPartialLabel="Partial day"
      onDateClick={onDateClick}
      filterSlot={
        <label className="inline-label">
          Department
          <select value={filterDepartmentId} onChange={(e) => setFilterDepartmentId(e.target.value)}>
            <option value="">All departments</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
      }
      toolbarExtra={
        isAdmin && (
          <>
            <div className="schedule-month-jump" ref={slotSettingsRef}>
              <button
                type="button"
                className="btn-secondary btn btn-sm"
                onClick={() => setSlotSettingsOpen((v) => !v)}
              >
                <Gear size={16} />
                Slot settings
              </button>
              {slotSettingsOpen && (
                <div className="schedule-month-jump-popover schedule-slot-settings-popover">
                  <SlotSettingsFields
                    defaultDuration={defaultDuration}
                    bufferMinutes={bufferMinutes}
                    onChangeDuration={onChangeDuration}
                    onChangeBuffer={onChangeBuffer}
                  />
                </div>
              )}
            </div>
            <button type="button" className="btn btn-sm" onClick={onAddSchedule}>
              + Add Schedule
            </button>
          </>
        )
      }
      renderCellContent={(dateStr) => {
        // Same day-status computation the Time Off tab's own calendar
        // uses (dayAvailability, doctorSchedule.ts) -- one shared read
        // of "what does this date look like", not a second
        // implementation. Time off here is purely a display lens: it
        // never touches what's actually bookable (availability_engine.py
        // remains the sole authority on that).
        const { status, segments, workingBlocks } = dayAvailability(dateStr, visibleBlocks, oneOffBlocks)
        const availableSegments = segments.filter((s) => s.type === 'available')
        if (status === 'time_off') {
          return <span className="schedule-month-cell-timeoff">Time off</span>
        }
        if (status === 'none') {
          return <span className="schedule-month-cell-none">No schedule</span>
        }
        if (status === 'partial') {
          return (
            <span className="schedule-month-cell-periods">
              <span className="schedule-month-cell-status partial">Partial day</span>
              {availableSegments.slice(0, 2).map((s, si) => (
                <span key={si} className="schedule-month-cell-period partial">
                  {formatTimeOfDay(s.start)} – {formatTimeOfDay(s.end)}
                </span>
              ))}
              {availableSegments.length > 2 && (
                <span className="schedule-month-cell-more">+{availableSegments.length - 2} more</span>
              )}
            </span>
          )
        }
        return (
          <span className="schedule-month-cell-periods">
            {workingBlocks.slice(0, 2).map((b, bi) => (
              <span key={bi} className="schedule-month-cell-period working">
                {formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}
              </span>
            ))}
            {workingBlocks.length > 2 && (
              <span className="schedule-month-cell-more">+{workingBlocks.length - 2} more</span>
            )}
          </span>
        )
      }}
    />
  )
}
