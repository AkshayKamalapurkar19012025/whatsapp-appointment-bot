import { useEffect, useId, useState } from 'react'
import { GraduationCap } from '@phosphor-icons/react'
import {
  ApiError,
  addDoctorEducation,
  featureDoctorEducation,
  getDoctorProfile,
  listDepartments,
  removeDoctorEducation,
  removeDoctorPhoto,
  updateDoctor,
  uploadDoctorPhoto,
} from '../api'
import type { Department, Doctor, DoctorEducationEntry, DoctorProfile } from '../types'
import { qualificationsFromEducation } from '../format'
import DoctorAvatar from '../DoctorAvatar'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../components/ui/alert-dialog'

// The "Profile" page reached from the doctor workspace's "More" menu
// (DoctorWorkspace.tsx). Two things, each with one source of truth:
//
// 1. Professional information -- read-only by default (name,
//    specialization, sub-specialization, years of experience,
//    qualifications), with an explicit Edit/Save/Cancel cycle instead
//    of always-open inputs. "Qualifications" is never a form field
//    here -- it's derived from Education & Credentials below (see
//    format.ts's qualificationsFromEducation) and pushed into the same
//    doctors.qualifications column/API field every other compact card
//    (the Doctors directory, its hover popover) already reads, so
//    those keep working unchanged with no second, driftable place to
//    type it.
// 2. Education & Credentials -- add/edit/remove entries, with the
//    "shown on patient profile" flag set right in the add/edit form
//    (migrations/0014_doctor_profile.sql's one-featured-entry-per-
//    doctor rule, unchanged). There's no PUT /education/{id} endpoint
//    on the backend -- "Edit" is a DELETE of the old row followed by a
//    POST of the new one (re-featuring afterwards if the edited entry
//    was the featured one), not a new API.
export default function DoctorProfileSection({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [profile, setProfile] = useState<DoctorProfile | null>(null)
  const [departments, setDepartments] = useState<Department[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const specializationListId = useId()

  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [specialization, setSpecialization] = useState('')
  const [subSpecialization, setSubSpecialization] = useState('')
  const [yearsOfExperience, setYearsOfExperience] = useState('')
  const [savingProfile, setSavingProfile] = useState(false)

  const [photoBusy, setPhotoBusy] = useState(false)

  const [showEduForm, setShowEduForm] = useState(false)
  const [editingEduId, setEditingEduId] = useState<number | null>(null)
  const [eduQualification, setEduQualification] = useState('')
  const [eduInstitution, setEduInstitution] = useState('')
  const [eduCity, setEduCity] = useState('')
  const [eduCountry, setEduCountry] = useState('')
  const [eduYear, setEduYear] = useState('')
  const [eduFeatured, setEduFeatured] = useState(false)
  const [eduBusy, setEduBusy] = useState(false)
  const [removeEduTarget, setRemoveEduTarget] = useState<DoctorEducationEntry | null>(null)

  function load() {
    setLoading(true)
    setError(null)
    getDoctorProfile(doctor.id)
      .then(setProfile)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctor profile'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [doctor.id])

  // Specialization-suggestion source for the edit form's datalist.
  useEffect(() => {
    listDepartments().then(setDepartments).catch(() => undefined)
  }, [])

  function startEdit() {
    if (!profile) return
    setName(profile.name)
    setSpecialization(profile.specialization ?? '')
    setSubSpecialization(profile.sub_specialization ?? '')
    setYearsOfExperience(profile.years_of_experience !== null ? String(profile.years_of_experience) : '')
    setError(null)
    setEditing(true)
  }

  async function handleSaveProfile(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSavingProfile(true)
    try {
      const updated = await updateDoctor(doctor.id, {
        name,
        specialization,
        sub_specialization: subSpecialization || undefined,
        qualifications: profile ? qualificationsFromEducation(profile.education) : undefined,
        years_of_experience: yearsOfExperience ? Number(yearsOfExperience) : undefined,
      })
      setProfile((prev) => (prev ? { ...prev, ...updated } : prev))
      setEditing(false)
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

  // Keeps doctors.qualifications (the compact-card field every other
  // listing reads) in sync with Education & Credentials -- called
  // after every add/remove/edit below so it's never a step the admin
  // has to remember separately. PUT /doctors/{id} overwrites every
  // scalar column on the row (no partial-update support), so this has
  // to resend the doctor's other current fields alongside the newly
  // computed qualifications, not qualifications alone.
  async function syncQualifications(freshProfile: DoctorProfile) {
    try {
      await updateDoctor(doctor.id, {
        name: freshProfile.name,
        specialization: freshProfile.specialization ?? '',
        sub_specialization: freshProfile.sub_specialization ?? undefined,
        years_of_experience: freshProfile.years_of_experience ?? undefined,
        qualifications: qualificationsFromEducation(freshProfile.education),
      })
    } catch {
      // Best-effort -- the education change itself already succeeded
      // and is what the refreshed profile above already shows; a
      // failure to also refresh the derived summary field elsewhere
      // isn't worth blocking on.
    }
  }

  function resetEduForm() {
    setShowEduForm(false)
    setEditingEduId(null)
    setEduQualification('')
    setEduInstitution('')
    setEduCity('')
    setEduCountry('')
    setEduYear('')
    setEduFeatured(false)
  }

  function startAddEducation() {
    resetEduForm()
    setShowEduForm(true)
  }

  function startEditEducation(entry: DoctorEducationEntry) {
    setEditingEduId(entry.id)
    setEduQualification(entry.qualification)
    setEduInstitution(entry.institution)
    setEduCity(entry.city)
    setEduCountry(entry.country)
    setEduYear(String(entry.completion_year))
    setEduFeatured(entry.is_primary)
    setError(null)
    setShowEduForm(true)
  }

  async function handleSubmitEducation(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setEduBusy(true)
    try {
      if (editingEduId !== null) {
        await removeDoctorEducation(doctor.id, editingEduId)
      }
      const created = await addDoctorEducation(doctor.id, {
        qualification: eduQualification,
        institution: eduInstitution,
        city: eduCity,
        country: eduCountry,
        completion_year: Number(eduYear),
      })
      if (eduFeatured) {
        await featureDoctorEducation(doctor.id, created.id)
      }
      resetEduForm()
      const refreshed = await getDoctorProfile(doctor.id)
      setProfile(refreshed)
      await syncQualifications(refreshed)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save this education entry')
    } finally {
      setEduBusy(false)
    }
  }

  async function confirmRemoveEducation() {
    if (!removeEduTarget) return
    setError(null)
    try {
      await removeDoctorEducation(doctor.id, removeEduTarget.id)
      const refreshed = await getDoctorProfile(doctor.id)
      setProfile(refreshed)
      await syncQualifications(refreshed)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove education entry')
    } finally {
      setRemoveEduTarget(null)
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

  const qualifications = qualificationsFromEducation(profile.education)

  return (
    <div>
      {error && <p className="error">{error}</p>}

      <div className="profile-photo-block">
        <DoctorAvatar photoUrl={profile.photo_url} name={profile.name} size={88} />
        {isAdmin && (
          <div className="profile-photo-actions">
            <label className="btn-secondary btn btn-sm profile-photo-upload-label">
              {photoBusy ? 'Uploading…' : 'Change photo'}
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                onChange={handlePhotoChange}
                disabled={photoBusy}
              />
            </label>
            {profile.photo_url && (
              <button type="button" className="link danger" onClick={handleRemovePhoto} disabled={photoBusy}>
                Remove
              </button>
            )}
          </div>
        )}
      </div>

      <div className="admin-content-header">
        <div>
          <h4 style={{ margin: 0 }}>Professional information</h4>
        </div>
        {isAdmin && !editing && (
          <button type="button" className="btn-secondary btn btn-sm" onClick={startEdit}>
            Edit profile
          </button>
        )}
      </div>

      {!editing ? (
        <dl className="summary">
          <dt>Name</dt>
          <dd>{profile.name}</dd>
          <dt>Specialization</dt>
          <dd>{profile.specialization ?? '—'}</dd>
          <dt>Sub-specialization</dt>
          <dd>{profile.sub_specialization ?? '—'}</dd>
          <dt>Years of experience</dt>
          <dd>{profile.years_of_experience ?? '—'}</dd>
          <dt>Qualifications</dt>
          <dd>{qualifications || '—'}</dd>
        </dl>
      ) : (
        <form className="doctor-form-grid" onSubmit={handleSaveProfile}>
          <label className="inline-label doctor-form-full">
            Name<span className="required-mark">*</span>
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </label>
          <label className="inline-label">
            Specialization<span className="required-mark">*</span>
            <input
              list={specializationListId}
              value={specialization}
              onChange={(e) => setSpecialization(e.target.value)}
              required
            />
            <datalist id={specializationListId}>
              {departments.map((d) => (
                <option key={d.id} value={d.name} />
              ))}
            </datalist>
          </label>
          <label className="inline-label">
            Sub-specialization
            <input value={subSpecialization} onChange={(e) => setSubSpecialization(e.target.value)} />
          </label>
          <label className="inline-label">
            Years of experience
            <input
              type="number"
              min={0}
              max={80}
              value={yearsOfExperience}
              onChange={(e) => setYearsOfExperience(e.target.value)}
            />
          </label>
          <div className="doctor-form-actions">
            <button type="button" className="btn-secondary btn btn-sm" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="submit" className="btn-sm" disabled={savingProfile}>
              {savingProfile ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </form>
      )}

      <div className="admin-content-header">
        <div>
          <h4 style={{ margin: 0 }}>Education &amp; credentials</h4>
          <p className="muted" style={{ margin: 0 }}>
            Qualifications shown above are generated automatically from these entries.
          </p>
        </div>
        {isAdmin && (
          <button type="button" className="btn btn-sm" onClick={startAddEducation}>
            {showEduForm && editingEduId === null ? 'Cancel' : '+ Add education'}
          </button>
        )}
      </div>

      {profile.education.length === 0 ? (
        <p className="muted">No education entries yet.</p>
      ) : (
        <div className="education-card-list">
          {profile.education.map((entry) => (
            <div key={entry.id} className="education-card">
              <span className="education-card-icon" aria-hidden="true">
                <GraduationCap size={18} weight="duotone" />
              </span>
              <div className="education-card-body">
                <strong>{entry.qualification}</strong>
                <span className="muted">
                  {entry.institution} · {entry.city}, {entry.country} · {entry.completion_year}
                </span>
                {entry.is_primary && <span className="pill status-confirmed">Shown on patient profile</span>}
              </div>
              {isAdmin && (
                <div className="education-card-actions">
                  <button type="button" className="link" onClick={() => startEditEducation(entry)}>
                    Edit
                  </button>
                  <button type="button" className="link danger" onClick={() => setRemoveEduTarget(entry)}>
                    Remove
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {isAdmin && showEduForm && (
        <form className="doctor-form-grid" onSubmit={handleSubmitEducation} style={{ marginTop: 'var(--space-4)' }}>
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
          <label className="inline-label doctor-form-full" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <input type="checkbox" checked={eduFeatured} onChange={(e) => setEduFeatured(e.target.checked)} style={{ width: 'auto', margin: 0 }} />
            Show on patient profile
          </label>
          <div className="doctor-form-actions">
            <button type="button" className="btn-secondary btn btn-sm" onClick={resetEduForm}>
              Cancel
            </button>
            <button type="submit" className="btn-sm" disabled={eduBusy}>
              {eduBusy ? 'Saving…' : editingEduId !== null ? 'Save changes' : 'Add entry'}
            </button>
          </div>
        </form>
      )}

      <AlertDialog open={removeEduTarget !== null} onOpenChange={(open) => !open && setRemoveEduTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this education entry?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeEduTarget && `Remove ${removeEduTarget.qualification} (${removeEduTarget.institution})?`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemoveEducation}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
