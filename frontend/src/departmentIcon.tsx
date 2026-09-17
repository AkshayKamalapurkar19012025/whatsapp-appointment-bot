import { Buildings } from '@phosphor-icons/react'
import type { Icon } from '@phosphor-icons/react'

// One fixed icon for every department, everywhere a department is shown
// (DepartmentsPanel.tsx, the Doctors directory/workspace, DepartmentChip,
// SchedulingFlow.tsx) -- Buildings, the same icon already used for the
// concept of "department" in DepartmentsPanel's and DoctorsPanel's own
// stat cards. Kept as a function (not a bare constant import at each call
// site) so every existing `departmentIcon(d.name)` call site keeps
// working unchanged, and so a newly created department gets the exact
// same icon automatically -- there is no per-department data to go stale.
export function departmentIcon(_departmentName: string): Icon {
  return Buildings
}
