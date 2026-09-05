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
            <div className="doctor-profile-header">
              <DoctorAvatar photoUrl={profile.photo_url} name={profile.name} size={88} />
              <div>
                <h2>{profile.name}</h2>
                {profile.specialization && (
                  <p className="muted">
                    {profile.specialization}
                    {profile.sub_specialization ? ` (${profile.sub_specialization})` : ''}
                  </p>
                )}
              </div>
            </div>

            {(profile.qualifications || profile.years_of_experience !== null) && (
              <dl className="summary">
                {profile.qualifications && (
                  <>
                    <dt>Qualifications</dt>
                    <dd>{profile.qualifications}</dd>
                  </>
                )}
                {profile.years_of_experience !== null && (
                  <>
                    <dt>Experience</dt>
                    <dd>
                      {profile.years_of_experience} {profile.years_of_experience === 1 ? 'year' : 'years'}
                    </dd>
                  </>
                )}
              </dl>
            )}

            <h3>Education &amp; Training</h3>
            {profile.education.length === 0 ? (
              <p className="muted">No education details on record.</p>
            ) : (
              <ul className="education-list">
                {profile.education.map((entry) => (
                  <li key={entry.id} className="education-entry">
                    <div>
                      <strong>{entry.qualification}</strong> — {entry.institution}, {entry.city},{' '}
                      {entry.country} ({entry.completion_year})
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
