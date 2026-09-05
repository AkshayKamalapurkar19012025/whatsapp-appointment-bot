import type { ReactNode } from 'react'
import DoctorAvatar from './DoctorAvatar'
import type { DoctorProfileSummary } from './types'

// The compact doctor-selection card shown during both Doctor-First and
// Date-First booking (BookingFlow.tsx): name, specialization, years of
// experience, key qualification, education/training location, and a
// "View Profile" escape hatch to the full profile -- deliberately never
// more than this, per the product decision not to overcrowd selection
// lists with the full profile. `extra` carries whatever the specific
// step also needs to show (Date-First's slot count), rendered as an
// extra muted line rather than each caller reimplementing the card.
export default function DoctorCard({
  doctor,
  extra,
  onSelect,
  onViewProfile,
}: {
  doctor: DoctorProfileSummary & { id: number; name: string }
  extra?: ReactNode
  onSelect: () => void
  onViewProfile: () => void
}) {
  const summaryParts = [
    doctor.years_of_experience !== null
      ? `${doctor.years_of_experience} ${doctor.years_of_experience === 1 ? 'year' : 'years'} experience`
      : null,
    doctor.qualifications,
  ].filter(Boolean)

  return (
    <li className="doctor-option">
      <button type="button" className="doctor-option-select" onClick={onSelect}>
        <DoctorAvatar photoUrl={doctor.photo_url} name={doctor.name} size={48} />
        <span className="doctor-option-info">
          <span className="doctor-option-name">{doctor.name}</span>
          {doctor.specialization && <span className="option-subtitle">{doctor.specialization}</span>}
          {summaryParts.length > 0 && <span className="muted doctor-option-meta">{summaryParts.join(' · ')}</span>}
          {doctor.education_location && (
            <span className="muted doctor-option-meta">{doctor.education_location}</span>
          )}
          {extra}
        </span>
      </button>
      <button type="button" className="link doctor-option-view-profile" onClick={onViewProfile}>
        View Profile
      </button>
    </li>
  )
}
