import { useEffect, useState } from 'react'
import { X } from '@phosphor-icons/react'
import { ApiError, getDoctorProfile } from './api'
import type { DoctorProfile } from './types'
import DoctorAvatar from './DoctorAvatar'

// "View Profile" from either compact booking card (DoctorCard.tsx) --
// the complete doctor profile and full education history, shown as an
// overlay on top of the current booking step rather than a route change,
// so closing it returns the patient to exactly where they were.
export default function DoctorProfileModal({
  doctorId,
  onClose,
}: {
  doctorId: number
  onClose: () => void
}) {
  const [profile, setProfile] = useState<DoctorProfile | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setProfile(null)
    setError(null)
    getDoctorProfile(doctorId)
      .then(setProfile)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctor profile'))
  }, [doctorId])

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Doctor profile"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        {error && <p className="error">{error}</p>}

        {!profile && !error && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading…
          </div>
        )}

        {profile && (
          <>
            {/* Header: everything that identifies this doctor at a glance
                (photo, name, specialization, sub-specialization,
                experience, key qualifications) -- the part of the
                profile a patient actually decides on, so it comes first
                and reads as a complete identity block, not a name over
                an empty page with a data table below it. */}
            <div className="doctor-profile-header">
              <DoctorAvatar photoUrl={profile.photo_url} name={profile.name} size={88} />
              <div className="doctor-profile-identity">
                <h2>{profile.name}</h2>
                {profile.specialization && (
                  <p className="doctor-profile-specialization">
                    {profile.specialization}
                    {profile.sub_specialization ? ` · ${profile.sub_specialization}` : ''}
                  </p>
                )}
                {(profile.years_of_experience !== null || profile.qualifications) && (
                  <p className="muted doctor-profile-meta">
                    {[
                      profile.years_of_experience !== null
                        ? `${profile.years_of_experience} ${profile.years_of_experience === 1 ? 'year' : 'years'} experience`
                        : null,
                      profile.qualifications,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                )}
              </div>
            </div>

            {/* Education & Training: lower priority than the header
                above (per the product decision this modal was flagged
                for -- a profile, not an education-only popup), so it's
                visually secondary: a divider, a smaller uppercase
                section label, and the same compact per-entry line the
                admin Profile tab already uses, including which entry (if
                any) is the doctor's chosen featured one. */}
            <hr className="doctor-profile-section-divider" />
            <h3 className="doctor-profile-section-label">Education &amp; Training</h3>
            {profile.education.length === 0 ? (
              <p className="muted">No education details have been added yet.</p>
            ) : (
              <ul className="education-list">
                {profile.education.map((entry) => (
                  <li key={entry.id} className={entry.is_primary ? 'education-entry featured' : 'education-entry'}>
                    <div>
                      <strong>{entry.qualification}</strong> — {entry.institution}, {entry.city},{' '}
                      {entry.country} ({entry.completion_year})
                      {entry.is_primary && <span className="pill role-admin">Featured</span>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  )
}
