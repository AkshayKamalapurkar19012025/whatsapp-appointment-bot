import { UserCircle } from '@phosphor-icons/react'

// Shared between the admin Doctors panel, the patient-facing compact
// booking cards (Doctor-First and Date-First), and the "View Profile"
// modal -- one place decides what a missing photo looks like, rather
// than each caller inventing its own fallback.
export default function DoctorAvatar({
  photoUrl,
  name,
  size = 48,
}: {
  photoUrl?: string | null
  name: string
  size?: number
}) {
  if (photoUrl) {
    return (
      <img
        src={photoUrl}
        alt={name}
        className="doctor-avatar"
        style={{ width: size, height: size }}
      />
    )
  }

  return (
    <span
      className="doctor-avatar doctor-avatar-fallback"
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <UserCircle size={size} weight="light" />
    </span>
  )
}
