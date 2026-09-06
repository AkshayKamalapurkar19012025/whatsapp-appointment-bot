import type { Icon } from '@phosphor-icons/react'
import { CalendarCheck, ClipboardText, Heartbeat, Pill, Stethoscope, Syringe } from '@phosphor-icons/react'

// Appointment types are an admin-managed free-text list (no icon field
// of their own), same situation as departmentIcon.tsx -- a best-effort
// keyword match against the type's existing name, purely presentational,
// with a generic fallback for anything that doesn't match.
const KEYWORD_ICONS: Array<[RegExp, Icon]> = [
  [/consult/i, Stethoscope],
  [/follow[\s-]?up|review/i, ClipboardText],
  [/check[\s-]?up|screening|physical/i, Heartbeat],
  [/vaccin|injection|shot/i, Syringe],
  [/medication|prescription|refill/i, Pill],
]

export function appointmentTypeIcon(typeName: string): Icon {
  const match = KEYWORD_ICONS.find(([pattern]) => pattern.test(typeName))
  return match ? match[1] : CalendarCheck
}
