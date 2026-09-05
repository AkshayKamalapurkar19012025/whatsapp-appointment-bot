import { useEffect, useState } from 'react'
import {
  ApiError,
  addDoctorEducation,
  featureDoctorEducation,
  getDoctorProfile,
  removeDoctorEducation,
  removeDoctorPhoto,
  unfeatureDoctorEducation,
  updateDoctor,
  uploadDoctorPhoto,
} from '../api'
import type { Doctor, DoctorEducationEntry, DoctorProfile } from '../types'
import DoctorAvatar from '../DoctorAvatar'

// The admin-only "Profile" tab of DoctorDetail.tsx: edit the scalar
// profile fields (specialization/sub-specialization/qualifications/
// years of experience), upload/replace/remove the profile photo, and
// manage the multi-entry Education & Training list -- including which
// single entry (if any) is "featured" onto the patient-facing compact
// booking card (see migrations/0014_doctor_profile.sql's partial unique
// index: at most one featured entry per doctor, never auto-chosen).
//
// Kept in its own file rather than folded into DoctorDetail.tsx, which
// was already long before this tab existed.
export default function DoctorProfileSection({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [profile, setProfile] = useState<DoctorProfile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [name, setName] = useState('')
  const [specialization, setSpecialization] = useState('')
  const [subSpecialization, setSubSpecialization] = useState('')
  const [qualifications, setQualifications] = useState('')
  const [yearsOfExperience, setYearsOfExperience] = useState('')
  const [savingProfile, setSavingProfile] = useState(false)

  const [photoBusy, setPhotoBusy] = useState(false)

  const [eduQualification, setEduQualification] = useState('')
  const [eduInstitution, setEduInstitution] = useState('')
  const [eduCity, setEduCity] = useState('')
  const [eduCountry, setEduCountry] = useState('')
  const [eduYear, setEduYear] = useState('')
  const [eduBusy, setEduBusy] = useState(false)

  function load() {
    setLoading(true)
    setError(null)
    getDoctorProfile(doctor.id)
      .then((p) => {
        setProfile(p)
        setName(p.name)
        setSpecialization(p.specialization ?? '')
        setSubSpecialization(p.sub_specialization ?? '')
        setQualifications(p.qualifications ?? '')
        setYearsOfExperience(p.years_of_experience !== null ? String(p.years_of_experience) : '')
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctor profile'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [doctor.id])

  async function handleSaveProfile(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSavingProfile(true)
    try {
      const updated = await updateDoctor(doctor.id, {
        name,
        specialization,
        sub_specialization: subSpecialization || undefined,
        qualifications: qualifications || undefined,
        years_of_experience: yearsOfExperience ? Number(yearsOfExperience) : undefined,
      })
      setProfile((prev) => (prev ? { ...prev, ...updated } : prev))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save doctor profile')
    } finally {
      setSavingProfile(false)
    }
  }

  async function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setError(null)
    setPhotoBusy(true)
    try {
      const result = await uploadDoctorPhoto(doctor.id, file)
      setProfile((prev) => (prev ? { ...prev, photo_url: result.photo_url } : prev))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not upload photo')
    } finally {
      setPhotoBusy(false)
      e.target.value = ''
    }
  }

  async function handleRemovePhoto() {
    setError(null)
    setPhotoBusy(true)
    try {
      await removeDoctorPhoto(doctor.id)
      setProfile((prev) => (prev ? { ...prev, photo_url: null } : prev))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove photo')
    } finally {
      setPhotoBusy(false)
    }
  }

  async function handleAddEducation(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setEduBusy(true)
    try {
      await addDoctorEducation(doctor.id, {
        qualification: eduQualification,
        institution: eduInstitution,
        city: eduCity,
        country: eduCountry,
        completion_year: Number(eduYear),
      })
      setEduQualification('')
      setEduInstitution('')
      setEduCity('')
      setEduCountry('')
      setEduYear('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add education entry')
    } finally {
      setEduBusy(false)
    }
  }

  async function handleRemoveEducation(educationId: number) {
    setError(null)
    try {
      await removeDoctorEducation(doctor.id, educationId)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove education entry')
    }
  }

  async function handleToggleFeature(entry: DoctorEducationEntry) {
    setError(null)
    try {
      if (entry.is_primary) {
        await unfeatureDoctorEducation(doctor.id, entry.id)
      } else {
        await featureDoctorEducation(doctor.id, entry.id)
      }
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update the featured education entry')
    }
  }

  if (loading) {
    return (
      <div className="state-block">
        <span className="spinner" aria-hidden="true" />
        Loading profile…
      </div>
    )
  }

  if (!profile) {
    return error ? <p className="error">{error}</p> : null
  }

  return (
    <div>
      {error && <p className="error">{error}</p>}

      <div className="doctor-profile-photo-row">
        <DoctorAvatar photoUrl={profile.photo_url} name={profile.name} size={96} />
        {isAdmin && (
          <div className="doctor-profile-photo-actions">
            <label className="btn-secondary btn doctor-photo-upload-label">
              {photoBusy ? 'Uploading…' : profile.photo_url ? 'Replace photo' : 'Upload photo'}
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                onChange={handlePhotoChange}
                disabled={photoBusy}
              />
            </label>
            {profile.photo_url && (
              <button type="button" className="link danger" onClick={handleRemovePhoto} disabled={photoBusy}>
                Remove photo
              </button>
            )}
          </div>
        )}
      </div>

      {isAdmin ? (
        <form className="inline-form wrap" onSubmit={handleSaveProfile}>
          <label className="inline-label">
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </label>
          <label className="inline-label">
            Specialization
            <input value={specialization} onChange={(e) => setSpecialization(e.target.value)} required />
          </label>
          <label className="inline-label">
            Sub-specialization <span className="muted">(optional)</span>
            <input value={subSpecialization} onChange={(e) => setSubSpecialization(e.target.value)} />
          </label>
          <label className="inline-label">
            Qualifications <span className="muted">(optional)</span>
            <input value={qualifications} onChange={(e) => setQualifications(e.target.value)} />
          </label>
          <label className="inline-label">
            Years of experience <span className="muted">(optional)</span>
            <input
              type="number"
              min={0}
              max={80}
              value={yearsOfExperience}
              onChange={(e) => setYearsOfExperience(e.target.value)}
            />
          </label>
          <button type="submit" style={{ width: 'auto' }} disabled={savingProfile}>
            {savingProfile ? 'Saving…' : 'Save profile'}
          </button>
        </form>
      ) : (
        <dl className="summary">
          <dt>Specialization</dt>
          <dd>{profile.specialization ?? '—'}</dd>
          <dt>Qualifications</dt>
          <dd>{profile.qualifications ?? '—'}</dd>
          <dt>Years of experience</dt>
          <dd>{profile.years_of_experience ?? '—'}</dd>
        </dl>
      )}

      <h4>Education &amp; Training</h4>
      {profile.education.length === 0 ? (
        <p className="muted">No education entries yet.</p>
      ) : (
        <ul className="education-list">
          {profile.education.map((entry) => (
            <li key={entry.id} className={entry.is_primary ? 'education-entry featured' : 'education-entry'}>
              <div>
                <strong>{entry.qualification}</strong> — {entry.institution}, {entry.city}, {entry.country} (
                {entry.completion_year})
                {entry.is_primary && <span className="pill role-admin">Featured on card</span>}
              </div>
              {isAdmin && (
                <div className="education-entry-actions">
                  <button type="button" className="link" onClick={() => handleToggleFeature(entry)}>
                    {entry.is_primary ? 'Unfeature' : 'Feature on card'}
                  </button>
                  <button type="button" className="link danger" onClick={() => handleRemoveEducation(entry.id)}>
                    Remove
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {isAdmin && (
        <form className="inline-form wrap" onSubmit={handleAddEducation}>
          <label className="inline-label">
            Qualification
            <input
              placeholder="MBBS"
              value={eduQualification}
              onChange={(e) => setEduQualification(e.target.value)}
              required
            />
          </label>
          <label className="inline-label">
            Institution
            <input
              placeholder="AIIMS"
              value={eduInstitution}
              onChange={(e) => setEduInstitution(e.target.value)}
              required
            />
          </label>
          <label className="inline-label">
            City
            <input placeholder="New Delhi" value={eduCity} onChange={(e) => setEduCity(e.target.value)} required />
          </label>
          <label className="inline-label">
            Country
            <input placeholder="India" value={eduCountry} onChange={(e) => setEduCountry(e.target.value)} required />
          </label>
          <label className="inline-label">
            Completion year
            <input
              type="number"
              min={1950}
              max={new Date().getFullYear()}
              placeholder="2010"
              value={eduYear}
              onChange={(e) => setEduYear(e.target.value)}
              required
            />
          </label>
          <button type="submit" style={{ width: 'auto' }} disabled={eduBusy}>
            {eduBusy ? 'Adding…' : 'Add entry'}
          </button>
        </form>
      )}
    </div>
  )
}
