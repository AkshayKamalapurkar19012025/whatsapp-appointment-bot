// A small fixed palette of soft accent tints for icon cards representing
// a *category* (a department, an appointment type) rather than an
// individual (a doctor) -- deterministically picked from the item's own
// name so the same department/type always gets the same tint across
// renders and sessions, without needing a color column in the database.
// Kept to a handful of restrained, healthcare-appropriate tones rather
// than a full rainbow, matching every other accent already in the
// design system.
const ACCENT_CLASSES = ['accent-teal', 'accent-blue', 'accent-purple', 'accent-amber', 'accent-rose']

export function accentClassFor(key: string): string {
  let hash = 0
  for (let i = 0; i < key.length; i++) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0
  }
  return ACCENT_CLASSES[Math.abs(hash) % ACCENT_CLASSES.length]
}
