import { useState } from 'react'
import { createPortal } from 'react-dom'
import { CaretDown, CaretRight, X } from '@phosphor-icons/react'
import { ApiError, createPatientAdmin, updatePatientAdmin } from '../api'
import type { Patient, PatientGender } from '../types'
import PhoneInput from '../PhoneInput'
import DobPicker from './DobPicker'

const GENDER_OPTIONS: { value: PatientGender; label: string }[] = [
  { value: 'MALE', label: 'Male' },
  { value: 'FEMALE', label: 'Female' },
  { value: 'OTHER', label: 'Other' },
]

// The ONE patient registration/edit implementation in the app -- same
// createPortal + modal-overlay/modal-panel shape as AddDepartmentModal.tsx/
// AddDoctorModal.tsx, reused (not reimplemented) from three contexts:
// PatientsPanel's "Add Patient"/"Edit Patient", and BookAppointmentPanel's
// "+ Register new patient" (walk-in and every other booking source alike
// -- walk-in was never a separate patient type, just a booking source).
// Name + mobile number are the only required fields (fast registration,
// especially for a walk-in); date of birth and gender are optional and
// collapsed behind "Add more details" so the fast path stays exactly two
// fields unless staff choose to expand it.
export default function PatientFormModal({
  mode,
  patient,
  title,
  onClose,
  onSaved,
}: {
  mode: 'create' | 'edit'
  // Required (and used as the initial values) for 'edit'; ignored for 'create'.
  patient?: Patient | null
  title: string
  onClose: () => void
  onSaved: (patient: Patient) => void
}) {
  const [name, setName] = useState(patient?.name ?? '')
  const [phone, setPhone] = useState(patient?.whatsapp_number ?? '')
  const [dateOfBirth, setDateOfBirth] = useState(patient?.date_of_birth ?? '')
  const [gender, setGender] = useState<PatientGender | ''>(patient?.gender ?? '')
  // Open by default only when editing a patient who already has one of
  // these set -- otherwise stays collapsed so a brand-new registration
  // (the common walk-in case) starts on the fast, two-field path.
  const [showMore, setShowMore] = useState(Boolean(patient?.date_of_birth || patient?.gender))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const saved =
        mode === 'edit' && patient
          ? await updatePatientAdmin(patient.id, name.trim(), phone, dateOfBirth || null, gender || null)
          : await createPatientAdmin(name.trim(), phone, dateOfBirth || null, gender || null)
      onSaved(saved)
      onClose()
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : mode === 'edit' ? 'Could not update this patient' : 'Could not register the new patient',
      )
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">{title}</h3>
        {error && <p className="error">{error}</p>}

        <form onSubmit={handleSubmit}>
          <label className="inline-label" style={{ width: '100%' }}>
            Full name
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Patient's name" required />
          </label>
          <label className="inline-label" style={{ width: '100%', marginTop: 'var(--space-3)' }}>
            Mobile number
            <PhoneInput value={phone} onChange={setPhone} />
          </label>

          <button
            type="button"
            className="link patient-form-more-toggle"
            onClick={() => setShowMore((v) => !v)}
            aria-expanded={showMore}
          >
            {showMore ? <CaretDown size={13} weight="bold" /> : <CaretRight size={13} weight="bold" />} Add more details
          </button>

          {showMore && (
            <div className="inline-form wrap" style={{ marginTop: 0 }}>
              <label className="inline-label">
                Date of birth
                <DobPicker value={dateOfBirth} onChange={setDateOfBirth} />
              </label>
              <label className="inline-label">
                Gender
                <select value={gender} onChange={(e) => setGender(e.target.value as PatientGender | '')}>
                  <option value="">Not specified</option>
                  {GENDER_OPTIONS.map((g) => (
                    <option key={g.value} value={g.value}>
                      {g.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          <div className="payment-form-actions" style={{ marginTop: 'var(--space-4)' }}>
            <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-sm" disabled={busy || !name.trim() || !phone}>
              {busy ? 'Saving…' : mode === 'edit' ? 'Save changes' : 'Register patient'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
