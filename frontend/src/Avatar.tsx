// Initials-on-a-colored-circle avatar, standing in for the doctor photos
// the reference mockup used -- this app has no photo_url/image field on
// doctors (or anywhere else), so a real headshot isn't available. Colors
// are picked deterministically from the name so the same doctor always
// gets the same color across screens, and different doctors visually
// read as distinct people the way photos would.
const PALETTE = [
  '#c9432e', // primary coral
  '#2563eb', // blue
  '#0d9488', // teal
  '#7c3aed', // violet
  '#c2410c', // burnt orange
  '#0891b2', // cyan
  '#65a30d', // olive
  '#be185d', // rose
]

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  const letters = words.slice(0, 2).map((w) => w[0]?.toUpperCase() ?? '')
  return letters.join('') || '?'
}

function colorFor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return PALETTE[hash % PALETTE.length]
}

export default function Avatar({ name, size = 40 }: { name: string; size?: number }) {
  return (
    <span
      className="avatar"
      aria-hidden="true"
      style={{
        width: size,
        height: size,
        background: colorFor(name),
        fontSize: size * 0.38,
      }}
    >
      {initials(name)}
    </span>
  )
}
