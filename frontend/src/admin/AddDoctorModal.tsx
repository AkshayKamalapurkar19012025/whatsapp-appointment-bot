import { useId, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, X } from '@phosphor-icons/react'
import { ApiError, assignDoctorToDepartment, createDoctor } from '../api'
import type { Department, Doctor } from '../types'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'

type Step = 1 | 2 | 3

const STEP_LABELS: { title: string; subtitle: string }[] = [
  { title: 'Basic Information', subtitle: 'Name, contact, department' },
  { title: 'Professional Details', subtitle: 'Specialization, experience' },
  { title: 'Review & Save', subtitle: 'Confirm details' },
]

interface WizardState {
  name: string
  departmentId: string
  specialization: string
  subSpecialization: string
  yearsOfExperience: string
}

// 3-step Add Doctor wizard -- Basic Information / Professional Details /
// Review & Save, sharing one form state across all three steps (this
// replaces the previous single-page AddDoctorModal form; same
// createDoctor API/fields, just staged across steps instead of one flat
// grid). Deliberately has NO Availability/Schedule step -- doctor
// scheduling stays exactly where it already lives, Doctor Profile ->
// Schedule, opened automatically once this wizard hands off to
// DoctorWorkspace via onCreated (see DoctorsPanel.tsx).
//
// Fields deliberately NOT included, despite being common on an "Add
// Doctor" form elsewhere: Phone, Email, Registration Number and
// Biography have no column on `doctors` and no backend field to submit
// them to (see app/api/doctors.py's DoctorCreate) -- adding text inputs
// for them here would silently discard whatever was typed. Qualification
// is also left out on purpose even though `doctors.qualifications` does
// exist: it's a derived, admin-curated summary of the doctor's Education
// & Training entries (see format.ts's qualificationsFromEducation and
// DoctorProfileSection.tsx), never a value typed once at creation and
// left alone -- typing it here would create a second, driftable copy of
// the same fact the Education & Training section already owns. All four
// are called out in this session's implementation report as backend/UX
// gaps rather than being faked here.
export default function AddDoctorModal({
  departments,
  onClose,
  onCreated,
}: {
  departments: Department[]
  onClose: () => void
  onCreated: (doctor: Doctor) => void
}) {
  const [step, setStep] = useState<Step>(1)
  const [form, setForm] = useState<WizardState>({
    name: '',
    departmentId: '',
    specialization: '',
    subSpecialization: '',
    yearsOfExperience: '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const specializationListId = useId()

  function set<K extends keyof WizardState>(key: K, value: WizardState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const step1Valid = form.name.trim().length > 0 && form.departmentId.trim().length > 0 && form.specialization.trim().length > 0
  // Matches the backend's own constraint exactly (DoctorCreate.years_of_
  // experience: int, ge=0, le=80) -- Number.isInteger rejects a typed
  // decimal like "7.8" client-side, instead of letting it reach
  // createDoctor() and bounce back as a 422 from Pydantic's own int
  // parsing (a request-validation error, not one of this app's own
  // hand-written HTTPException messages).
  const yearsOfExperienceNum = Number(form.yearsOfExperience)
  const step2Valid =
    form.yearsOfExperience.trim().length > 0 &&
    Number.isInteger(yearsOfExperienceNum) &&
    yearsOfExperienceNum >= 0 &&
    yearsOfExperienceNum <= 80

  const departmentName = departments.find((d) => String(d.id) === form.departmentId)?.name ?? null

  function goToStep(target: Step) {
    // Only completed steps (and the current one) are directly reachable
    // from the stepper -- clicking ahead into a step whose own fields
    // haven't validated yet is blocked, same guard as the Next buttons.
    if (target === 2 && !step1Valid) return
    if (target === 3 && !(step1Valid && step2Valid)) return
    setStep(target)
  }

  async function handleSubmit() {
    if (busy) return // guards the double-click/double-submit case
    setError(null)
    setBusy(true)
    try {
      const created = await createDoctor({
        name: form.name.trim(),
        specialization: form.specialization.trim(),
        sub_specialization: form.subSpecialization.trim() || undefined,
        years_of_experience: form.yearsOfExperience ? Number(form.yearsOfExperience) : undefined,
      })

      // Department assignment is a separate relationship (doctor_
      // departments), not a column on doctors -- same two-call sequence
      // ManageDepartmentDoctorsModal already uses elsewhere. The doctor
      // itself is already created at this point; if only the assignment
      // fails, surface that but still hand off to the profile (nothing
      // about doctor creation itself failed, and the admin can assign
      // the department from the profile instead of losing the new doctor).
      if (form.departmentId) {
        try {
          await assignDoctorToDepartment(created.id, Number(form.departmentId))
        } catch {
          onCreated(created)
          onClose()
          return
        }
      }

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
        className="modal-panel doctor-wizard-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Add doctor"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <div className="doctor-wizard-layout">
          <nav className="doctor-wizard-stepper" aria-label="Add doctor steps">
            <h3 className="doctor-wizard-title">Add Doctor</h3>
            <p className="muted doctor-wizard-subtitle">Add a new doctor to the system</p>
            <ol>
              {STEP_LABELS.map((label, i) => {
                const stepNum = (i + 1) as Step
                const state = stepNum < step ? 'done' : stepNum === step ? 'current' : 'upcoming'
                return (
                  <li key={label.title} className={`doctor-wizard-step ${state}`}>
                    <button
                      type="button"
                      className="doctor-wizard-step-btn"
                      onClick={() => goToStep(stepNum)}
                      disabled={state === 'upcoming'}
                    >
                      <span className="doctor-wizard-step-dot" aria-hidden="true">
                        {state === 'done' ? <Check size={12} weight="bold" /> : stepNum}
                      </span>
                      <span className="doctor-wizard-step-text">
                        <span className="doctor-wizard-step-label">{label.title}</span>
                        <span className="muted doctor-wizard-step-sublabel">{label.subtitle}</span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ol>
            <p className="doctor-wizard-note">
              Doctor information will be used for appointments, scheduling and patient care.
            </p>
          </nav>

          <div className="doctor-wizard-content">
            {error && <p className="error">{error}</p>}

            {step === 1 && (
              <>
                <h4 className="doctor-wizard-content-heading">Basic Information</h4>
                <p className="muted doctor-wizard-content-subtitle">Enter the doctor&apos;s basic details</p>

                <div className="doctor-form-grid">
                  <label className="inline-label doctor-form-full">
                    Full Name<span className="required-mark">*</span>
                    <input
                      autoFocus
                      placeholder="Dr Jane Doe"
                      value={form.name}
                      onChange={(e) => set('name', e.target.value)}
                    />
                  </label>
                  <label className="inline-label">
                    Department<span className="required-mark">*</span>
                    <Select value={form.departmentId} onValueChange={(v) => set('departmentId', v)}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select department" />
                      </SelectTrigger>
                      <SelectContent>
                        {departments.map((d) => (
                          <SelectItem key={d.id} value={String(d.id)}>
                            {d.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </label>
                  <label className="inline-label">
                    Specialization<span className="required-mark">*</span>
                    <input
                      placeholder="Cardiology"
                      list={specializationListId}
                      value={form.specialization}
                      onChange={(e) => set('specialization', e.target.value)}
                    />
                    <datalist id={specializationListId}>
                      {departments.map((d) => (
                        <option key={d.id} value={d.name} />
                      ))}
                    </datalist>
                  </label>
                </div>

                <div className="doctor-wizard-footer">
                  <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
                    Cancel
                  </button>
                  <button type="button" className="btn btn-sm" disabled={!step1Valid} onClick={() => setStep(2)}>
                    Next →
                  </button>
                </div>
              </>
            )}

            {step === 2 && (
              <>
                <h4 className="doctor-wizard-content-heading">Professional Details</h4>
                <p className="muted doctor-wizard-content-subtitle">Enter the doctor&apos;s professional information</p>

                <div className="doctor-form-grid">
                  <label className="inline-label">
                    Sub-specialization
                    <input
                      placeholder="Interventional Cardiology"
                      value={form.subSpecialization}
                      onChange={(e) => set('subSpecialization', e.target.value)}
                    />
                  </label>
                  <label className="inline-label">
                    Years of Experience<span className="required-mark">*</span>
                    <input
                      type="number"
                      min={0}
                      max={80}
                      step={1}
                      placeholder="10"
                      value={form.yearsOfExperience}
                      onChange={(e) => set('yearsOfExperience', e.target.value)}
                    />
                    {form.yearsOfExperience.trim().length > 0 && !step2Valid && (
                      <span className="doctor-wizard-field-error">Enter a whole number of years, 0–80.</span>
                    )}
                  </label>
                </div>
                <p className="muted doctor-wizard-content-subtitle">
                  Qualifications and education history are added from the doctor&apos;s profile after creation, under
                  Education &amp; Training.
                </p>

                <div className="doctor-wizard-footer">
                  <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(1)}>
                    ← Back
                  </button>
                  <button type="button" className="btn btn-sm" disabled={!step2Valid} onClick={() => setStep(3)}>
                    Next →
                  </button>
                </div>
              </>
            )}

            {step === 3 && (
              <>
                <h4 className="doctor-wizard-content-heading">Review your details</h4>
                <p className="muted doctor-wizard-content-subtitle">Confirm the information before adding the doctor</p>

                <div className="doctor-wizard-review-card">
                  <div className="doctor-wizard-review-card-header">
                    <span>Basic Information</span>
                    <button type="button" className="link" onClick={() => setStep(1)}>
                      Edit
                    </button>
                  </div>
                  <dl className="doctor-wizard-review-list">
                    <dt>Name</dt>
                    <dd>{form.name || '—'}</dd>
                    <dt>Department</dt>
                    <dd>{departmentName ?? '—'}</dd>
                    <dt>Specialization</dt>
                    <dd>{form.specialization || '—'}</dd>
                  </dl>
                </div>

                <div className="doctor-wizard-review-card">
                  <div className="doctor-wizard-review-card-header">
                    <span>Professional Details</span>
                    <button type="button" className="link" onClick={() => setStep(2)}>
                      Edit
                    </button>
                  </div>
                  <dl className="doctor-wizard-review-list">
                    <dt>Sub-specialization</dt>
                    <dd>{form.subSpecialization || 'Not provided'}</dd>
                    <dt>Years of Experience</dt>
                    <dd>{form.yearsOfExperience ? `${form.yearsOfExperience} years` : '—'}</dd>
                  </dl>
                </div>

                <div className="doctor-wizard-footer">
                  <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(2)} disabled={busy}>
                    ← Back
                  </button>
                  <button type="button" className="btn btn-sm" disabled={busy} onClick={handleSubmit}>
                    {busy ? 'Adding…' : 'Add Doctor'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
