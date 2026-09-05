import type { Icon } from '@phosphor-icons/react'
import { Baby, Bone, Brain, Ear, Eye, FirstAidKit, Heart, Stethoscope, Tooth } from '@phosphor-icons/react'

// Departments are a free-form admin-managed list (see DepartmentsPanel.tsx)
// with no icon/description field of their own -- adding one would be a
// data-model change, not a visual one. This is a purely presentational,
// best-effort keyword match against the department's existing name so the
// department-selection cards can show a relevant icon without inventing
// any new data; anything that doesn't match a keyword below falls back to
// a generic Stethoscope rather than guessing.
const KEYWORD_ICONS: Array<[RegExp, Icon]> = [
  [/cardio|heart/i, Heart],
  [/dental|dentist|tooth/i, Tooth],
  [/ortho|bone|joint/i, Bone],
  [/pediatr|paediatr|child/i, Baby],
  [/neuro|brain/i, Brain],
  [/ent\b|ear|nose|throat/i, Ear],
  [/ophthal|eye|vision/i, Eye],
  [/emergency|trauma|urgent/i, FirstAidKit],
]

export function departmentIcon(departmentName: string): Icon {
  const match = KEYWORD_ICONS.find(([pattern]) => pattern.test(departmentName))
  return match ? match[1] : Stethoscope
}
