import { accentClassFor } from './cardAccent'
import type { Department } from './types'

// One consistent way to show a department's name anywhere it's rendered
// as a small tag/pill -- the Departments admin page's own cards
// (DepartmentsPanel.tsx) and the patient-facing scheduling flow
// (SchedulingFlow.tsx) already show a keyword-matched icon +
// deterministic accent color per department (departmentIcon.tsx /
// cardAccent.ts); this brings that same look to every other place a
// department shows up as a tag (the Doctors directory's Departments
// column/mobile card, its hover-preview popover, and the doctor
// workspace's own Departments tab), instead of those spots falling
// back to a plain colorless pill.
//
// `icon` is resolved by the caller's own .map() and passed in, not
// looked up here -- same reason DepartmentsPanel.tsx's DepartmentCard
// takes an `icon` prop instead of calling departmentIcon() in its own
// body: oxlint's react(static-components) heuristic flags `const Icon =
// departmentIcon(...)` directly in a component's body (a false
// positive -- departmentIcon returns a reference to an existing,
// stable icon component, nothing is actually being defined -- but only
// when that call isn't itself inside a .map() callback).
export default function DepartmentChip({
  department,
  icon,
  onRemove,
}: {
  department: Department
  icon: React.ReactNode
  // Only DepartmentAssignment's editable tag list passes this.
  onRemove?: () => void
}) {
  return (
    <span className={`department-chip ${accentClassFor(department.name)}`}>
      <span className="department-chip-icon" aria-hidden="true">
        {icon}
      </span>
      {department.name}
      {onRemove && (
        <button type="button" className="link" onClick={onRemove}>
          remove
        </button>
      )}
    </span>
  )
}
