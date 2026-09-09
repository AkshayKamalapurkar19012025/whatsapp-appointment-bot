import { useId, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import { ApiError, createDoctor } from '../api'
import type { Department, Doctor } from '../types'

// Same createPortal + modal-overlay/modal-panel shape as
// AddDepartmentModal.tsx -- the doctor directory's create form used to
// sit inline above the doctor grid; pulled into a modal so the
// directory itself stays just filters + the list (the create form was
// one more thing to scroll past every time, not something opened often).
// Same createDoctor API and fields DoctorsPanel.tsx's old inline form
// used, nothing new.
export default function AddDoctorModal({
  departments,
  onClose,
  onCreated,
}: {
  departments: Department[]
  onClose: () => void
  onCreated: (doctor: Doctor) => void
}) {
  const [name, setName] = useState('')
  const [specialization, setSpecialization] = useState('')
  const [subSpecialization, setSubSpecialization] = useState('')
  const [yearsOfExperience, setYearsOfExperience] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const specializationListId = useId()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const created = await createDoctor({
        name,
        specialization,
        sub_specialization: subSpecialization || undefined,
        // No qualifications field here -- a brand-new doctor has no
        // Education & Credentials entries yet, and that's this app's
        // one source of truth for qualifications (see format.ts's
        // qualificationsFromEducation, synced from DoctorProfileSection
        // whenever education changes). Typing it by hand here would
        // just be a second, driftable copy.
        years_of_experience: yearsOfExperience ? Number(yearsOfExperience) : undefined,
      })
      onCreated(created)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create doctor')
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Add doctor"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">Add Doctor</h3>
        {error && <p className="error">{error}</p>}

        <form className="doctor-form-grid" onSubmit={handleSubmit}>
          <label className="inline-label doctor-form-full">
            Name<span className="required-mark">*</span>
            <input
              autoFocus
              placeholder="Dr. Jane Doe"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
          <label className="inline-label">
            Specialization<span className="required-mark">*</span>
            <input
              placeholder="Cardiology"
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
            <input
              placeholder="Interventional Cardiology"
              value={subSpecialization}
              onChange={(e) => setSubSpecialization(e.target.value)}
            />
          </label>
          <label className="inline-label">
            Years of experience
            <input
              type="number"
              min={0}
              max={80}
              placeholder="10"
              value={yearsOfExperience}
              onChange={(e) => setYearsOfExperience(e.target.value)}
            />
          </label>
          <div className="doctor-form-actions payment-form-actions">
            <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-sm" disabled={busy || !name.trim() || !specialization.trim()}>
              {busy ? 'Saving…' : 'Add Doctor'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
