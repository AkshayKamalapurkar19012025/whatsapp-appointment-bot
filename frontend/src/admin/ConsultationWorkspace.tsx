import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle, Warning } from '@phosphor-icons/react'
import {
  ApiError,
  completeConsultation,
  getEncounterSummary,
  getLatestVitals,
  getOrCreateConsultation,
  recordVitals,
  saveConsultationDraft,
} from '../api'
import type { Consultation, EncounterSummary, Vitals, VitalsPriority } from '../types'
import { formatAgeGender, formatDateTime } from '../format'

type Tab = 'triage' | 'consultation'

const FOLLOW_UP_OPTIONS = [
  { label: 'No follow-up', days: null },
  { label: '3 days', days: 3 },
  { label: '7 days', days: 7 },
  { label: '15 days', days: 15 },
  { label: '1 month', days: 30 },
  { label: 'Custom', days: 'custom' as const },
]

function addDays(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

// A blank vitals draft -- every field starts empty/undefined, the form
// is uncontrolled-by-value in the sense that empty inputs mean "don't
// send this field" (recordVitals's body only includes what's actually
// typed, via toVitalsPayload below).
type VitalsFormState = {
  bp_systolic: string
  bp_diastolic: string
  pulse: string
  temperature_celsius: string
  spo2: string
  respiratory_rate: string
  weight_kg: string
  height_cm: string
  pain_score: string
  chief_complaint: string
  priority: VitalsPriority
  nursing_notes: string
}

const BLANK_VITALS_FORM: VitalsFormState = {
  bp_systolic: '',
  bp_diastolic: '',
  pulse: '',
  temperature_celsius: '',
  spo2: '',
  respiratory_rate: '',
  weight_kg: '',
  height_cm: '',
  pain_score: '',
  chief_complaint: '',
  priority: 'ROUTINE',
  nursing_notes: '',
}

function vitalsFormFromRecord(v: Vitals): VitalsFormState {
  return {
    bp_systolic: v.bp_systolic?.toString() ?? '',
    bp_diastolic: v.bp_diastolic?.toString() ?? '',
    pulse: v.pulse?.toString() ?? '',
    temperature_celsius: v.temperature_celsius?.toString() ?? '',
    spo2: v.spo2?.toString() ?? '',
    respiratory_rate: v.respiratory_rate?.toString() ?? '',
    weight_kg: v.weight_kg?.toString() ?? '',
    height_cm: v.height_cm?.toString() ?? '',
    pain_score: v.pain_score?.toString() ?? '',
    chief_complaint: v.chief_complaint ?? '',
    priority: v.priority,
    nursing_notes: v.nursing_notes ?? '',
  }
}

function numberOrUndefined(text: string): number | undefined {
  const trimmed = text.trim()
  if (!trimmed) return undefined
  const n = Number(trimmed)
  return Number.isFinite(n) ? n : undefined
}

type ConsultationFormState = {
  chief_complaint: string
  history_notes: string
  examination_notes: string
  diagnosis: string
  clinical_notes: string
  follow_up_reason: string
}

function consultationFormFromRecord(c: Consultation): ConsultationFormState {
  return {
    chief_complaint: c.chief_complaint ?? '',
    history_notes: c.history_notes ?? '',
    examination_notes: c.examination_notes ?? '',
    diagnosis: c.diagnosis ?? '',
    clinical_notes: c.clinical_notes ?? '',
    follow_up_reason: c.follow_up_reason ?? '',
  }
}

// OPD/HIMS master spec Phase 5 -- the doctor/nurse "Full Workspace" for
// one patient's visit: triage/vitals and the clinical consultation
// itself, both scoped to the encounter behind this appointment
// (migrations/0028_encounters.sql / 0029_vitals_and_consultations.sql).
// Reached from the live queue (QueueSection.tsx) for a CHECKED_IN
// patient -- see app/services/clinical_services.py's module docstring
// for exactly when writes here are and aren't allowed; this component
// mirrors that same CHECKED_IN gate rather than guessing at a second
// copy of it.
export default function ConsultationWorkspace({
  appointmentId,
  onBack,
}: {
  appointmentId: number
  onBack: () => void
}) {
  const [tab, setTab] = useState<Tab>('triage')
  const [encounter, setEncounter] = useState<EncounterSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  // Set specifically when the patient isn't checked in and no
  // consultation has ever been started -- distinct from loadError
  // (a genuine failure): this is an expected, navigable state (e.g. a
  // doctor clicking into a still-Waiting patient's row), not a bug.
  const [notCheckedIn, setNotCheckedIn] = useState(false)

  const [vitalsForm, setVitalsForm] = useState<VitalsFormState>(BLANK_VITALS_FORM)
  const [latestVitals, setLatestVitals] = useState<Vitals | null>(null)
  const [vitalsSaving, setVitalsSaving] = useState(false)
  const [vitalsError, setVitalsError] = useState<string | null>(null)
  const [vitalsSavedAt, setVitalsSavedAt] = useState<number | null>(null)

  const [consultation, setConsultation] = useState<Consultation | null>(null)
  const [consultationForm, setConsultationForm] = useState<ConsultationFormState>(
    consultationFormFromRecord({} as Consultation),
  )
  const [followUpChoice, setFollowUpChoice] = useState<string | null>(null)
  const [followUpCustomDate, setFollowUpCustomDate] = useState('')
  const [consultationSaving, setConsultationSaving] = useState(false)
  const [consultationError, setConsultationError] = useState<string | null>(null)
  const [consultationSavedAt, setConsultationSavedAt] = useState<number | null>(null)
  const [completing, setCompleting] = useState(false)
  const [completeError, setCompleteError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    setNotCheckedIn(false)

    getEncounterSummary(appointmentId)
      .then((summary) => {
        if (cancelled) return
        setEncounter(summary)
        return Promise.all([
          getLatestVitals(appointmentId).catch(() => null),
          getOrCreateConsultation(appointmentId)
            .then((c) => {
              setConsultation(c)
              setConsultationForm(consultationFormFromRecord(c))
              setFollowUpChoice(c.follow_up_date ? 'custom' : null)
              setFollowUpCustomDate(c.follow_up_date ?? '')
              return c
            })
            .catch((err) => {
              if (err instanceof ApiError && err.status === 409) {
                setNotCheckedIn(true)
                return null
              }
              throw err
            }),
        ])
      })
      .then((result) => {
        if (cancelled || !result) return
        const [vitals] = result
        if (vitals) {
          setLatestVitals(vitals)
          setVitalsForm(vitalsFormFromRecord(vitals))
        }
      })
      .catch((err) => {
        if (cancelled) return
        setLoadError(err instanceof ApiError ? err.message : 'Could not load this patient')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [appointmentId])

  async function handleSaveVitals() {
    setVitalsSaving(true)
    setVitalsError(null)
    try {
      const saved = await recordVitals(appointmentId, {
        bp_systolic: numberOrUndefined(vitalsForm.bp_systolic),
        bp_diastolic: numberOrUndefined(vitalsForm.bp_diastolic),
        pulse: numberOrUndefined(vitalsForm.pulse),
        temperature_celsius: numberOrUndefined(vitalsForm.temperature_celsius),
        spo2: numberOrUndefined(vitalsForm.spo2),
        respiratory_rate: numberOrUndefined(vitalsForm.respiratory_rate),
        weight_kg: numberOrUndefined(vitalsForm.weight_kg),
        height_cm: numberOrUndefined(vitalsForm.height_cm),
        pain_score: numberOrUndefined(vitalsForm.pain_score),
        chief_complaint: vitalsForm.chief_complaint.trim() || undefined,
        priority: vitalsForm.priority,
        nursing_notes: vitalsForm.nursing_notes.trim() || undefined,
      })
      setLatestVitals(saved)
      setVitalsSavedAt(Date.now())
    } catch (err) {
      setVitalsError(err instanceof ApiError ? err.message : 'Could not save vitals')
    } finally {
      setVitalsSaving(false)
    }
  }

  function currentFollowUpDate(): string | undefined {
    if (followUpChoice === null) return undefined
    if (followUpChoice === 'custom') return followUpCustomDate || undefined
    const option = FOLLOW_UP_OPTIONS.find((o) => o.label === followUpChoice)
    return option && typeof option.days === 'number' ? addDays(option.days) : undefined
  }

  async function handleSaveConsultation() {
    setConsultationSaving(true)
    setConsultationError(null)
    try {
      const saved = await saveConsultationDraft(appointmentId, {
        chief_complaint: consultationForm.chief_complaint.trim() || undefined,
        history_notes: consultationForm.history_notes.trim() || undefined,
        examination_notes: consultationForm.examination_notes.trim() || undefined,
        diagnosis: consultationForm.diagnosis.trim() || undefined,
        clinical_notes: consultationForm.clinical_notes.trim() || undefined,
        follow_up_date: currentFollowUpDate(),
        follow_up_reason: consultationForm.follow_up_reason.trim() || undefined,
      })
      setConsultation(saved)
      setConsultationSavedAt(Date.now())
    } catch (err) {
      setConsultationError(err instanceof ApiError ? err.message : 'Could not save the consultation')
    } finally {
      setConsultationSaving(false)
    }
  }

  async function handleCompleteConsultation() {
    setCompleting(true)
    setCompleteError(null)
    try {
      // Complete always reflects whatever's currently in the form, so a
      // doctor who typed a diagnosis and immediately clicks Complete
      // (without a separate Save Draft first) doesn't lose it.
      await saveConsultationDraft(appointmentId, {
        chief_complaint: consultationForm.chief_complaint.trim() || undefined,
        history_notes: consultationForm.history_notes.trim() || undefined,
        examination_notes: consultationForm.examination_notes.trim() || undefined,
        diagnosis: consultationForm.diagnosis.trim() || undefined,
        clinical_notes: consultationForm.clinical_notes.trim() || undefined,
        follow_up_date: currentFollowUpDate(),
        follow_up_reason: consultationForm.follow_up_reason.trim() || undefined,
      })
      const completed = await completeConsultation(appointmentId)
      setConsultation(completed)
    } catch (err) {
      setCompleteError(err instanceof ApiError ? err.message : 'Could not complete the consultation')
    } finally {
      setCompleting(false)
    }
  }

  if (loading) {
    return (
      <section>
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      </section>
    )
  }

  if (loadError) {
    return (
      <section>
        <button type="button" className="link doctor-workspace-back" onClick={onBack}>
          <ArrowLeft size={16} weight="bold" /> Back to queue
        </button>
        <p className="error">{loadError}</p>
      </section>
    )
  }

  const readOnly = !encounter || encounter.appointment_status !== 'CHECKED_IN' || consultation?.status === 'COMPLETED'

  return (
    <section>
      <button type="button" className="link doctor-workspace-back" onClick={onBack}>
        <ArrowLeft size={16} weight="bold" /> Back to queue
      </button>

      {encounter && (
        <div className="patient-context-header">
          <div className="patient-context-identity">
            <h2>{encounter.patient_name}</h2>
            <div className="patient-context-meta">
              <span>UHID {encounter.patient_uhid}</span>
              {formatAgeGender(encounter.patient_date_of_birth, encounter.patient_gender) && (
                <span>{formatAgeGender(encounter.patient_date_of_birth, encounter.patient_gender)}</span>
              )}
              <span>{encounter.doctor_name}</span>
              {encounter.token_number !== null && <span>Token #{encounter.token_number}</span>}
            </div>
          </div>
          <span className={`pill status-${encounter.appointment_status.toLowerCase()}`}>
            {encounter.appointment_status === 'CHECKED_IN' ? 'In progress' : encounter.appointment_status}
          </span>
        </div>
      )}

      {notCheckedIn && (
        <div className="state-block">
          <Warning size={20} weight="regular" />
          This patient isn&apos;t currently checked in. Triage and consultation become available once they&apos;ve
          checked in and a queue token has been issued.
        </div>
      )}

      {!notCheckedIn && (
        <>
          <div className="tabs">
            <button
              type="button"
              className={tab === 'triage' ? 'tab active' : 'tab'}
              onClick={() => setTab('triage')}
            >
              Triage / Vitals
            </button>
            <button
              type="button"
              className={tab === 'consultation' ? 'tab active' : 'tab'}
              onClick={() => setTab('consultation')}
            >
              Consultation
            </button>
          </div>

          {tab === 'triage' && (
            <div className="detail-section">
              {latestVitals && (
                <p className="muted">Last recorded {formatDateTime(latestVitals.recorded_at)}</p>
              )}
              {vitalsError && <p className="error">{vitalsError}</p>}

              <div className="doctor-form-grid">
                <label className="inline-label">
                  BP systolic
                  <input
                    type="number"
                    value={vitalsForm.bp_systolic}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, bp_systolic: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  BP diastolic
                  <input
                    type="number"
                    value={vitalsForm.bp_diastolic}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, bp_diastolic: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Pulse (bpm)
                  <input
                    type="number"
                    value={vitalsForm.pulse}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, pulse: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Temperature (°C)
                  <input
                    type="number"
                    step="0.1"
                    value={vitalsForm.temperature_celsius}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, temperature_celsius: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  SpO2 (%)
                  <input
                    type="number"
                    value={vitalsForm.spo2}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, spo2: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Respiratory rate
                  <input
                    type="number"
                    value={vitalsForm.respiratory_rate}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, respiratory_rate: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Weight (kg)
                  <input
                    type="number"
                    step="0.1"
                    value={vitalsForm.weight_kg}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, weight_kg: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Height (cm)
                  <input
                    type="number"
                    step="0.1"
                    value={vitalsForm.height_cm}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, height_cm: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Pain score (0-10)
                  <input
                    type="number"
                    min={0}
                    max={10}
                    value={vitalsForm.pain_score}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, pain_score: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Priority
                  <select
                    value={vitalsForm.priority}
                    disabled={readOnly}
                    onChange={(e) => setVitalsForm({ ...vitalsForm, priority: e.target.value as VitalsPriority })}
                  >
                    <option value="ROUTINE">Routine</option>
                    <option value="URGENT">Urgent</option>
                    <option value="EMERGENCY">Emergency</option>
                  </select>
                </label>
              </div>

              <label className="inline-label">
                Chief complaint
                <textarea
                  rows={2}
                  value={vitalsForm.chief_complaint}
                  disabled={readOnly}
                  onChange={(e) => setVitalsForm({ ...vitalsForm, chief_complaint: e.target.value })}
                />
              </label>
              <label className="inline-label">
                Nursing notes
                <textarea
                  rows={3}
                  value={vitalsForm.nursing_notes}
                  disabled={readOnly}
                  onChange={(e) => setVitalsForm({ ...vitalsForm, nursing_notes: e.target.value })}
                />
              </label>

              {!readOnly && (
                <button type="button" className="btn" disabled={vitalsSaving} onClick={handleSaveVitals}>
                  {vitalsSaving ? 'Saving…' : 'Save vitals'}
                </button>
              )}
              {vitalsSavedAt !== null && !vitalsSaving && (
                <span className="muted" style={{ marginLeft: 'var(--space-2)' }}>
                  <CheckCircle size={14} weight="fill" /> Saved
                </span>
              )}
            </div>
          )}

          {tab === 'consultation' && (
            <div className="detail-section">
              {readOnly && (
                <p className="muted">
                  {consultation?.status === 'COMPLETED'
                    ? `Completed ${consultation.completed_at ? formatDateTime(consultation.completed_at) : ''}`
                    : 'This visit is no longer in progress -- the consultation can be viewed but not edited.'}
                </p>
              )}
              {consultationError && <p className="error">{consultationError}</p>}
              {completeError && <p className="error">{completeError}</p>}

              <label className="inline-label">
                Chief complaint *
                <textarea
                  rows={2}
                  value={consultationForm.chief_complaint}
                  disabled={readOnly}
                  onChange={(e) => setConsultationForm({ ...consultationForm, chief_complaint: e.target.value })}
                />
              </label>
              <label className="inline-label">
                History
                <textarea
                  rows={3}
                  value={consultationForm.history_notes}
                  disabled={readOnly}
                  onChange={(e) => setConsultationForm({ ...consultationForm, history_notes: e.target.value })}
                />
              </label>
              <label className="inline-label">
                Examination
                <textarea
                  rows={3}
                  value={consultationForm.examination_notes}
                  disabled={readOnly}
                  onChange={(e) => setConsultationForm({ ...consultationForm, examination_notes: e.target.value })}
                />
              </label>
              <label className="inline-label">
                Diagnosis *
                <textarea
                  rows={2}
                  value={consultationForm.diagnosis}
                  disabled={readOnly}
                  onChange={(e) => setConsultationForm({ ...consultationForm, diagnosis: e.target.value })}
                />
              </label>
              <label className="inline-label">
                Clinical notes
                <textarea
                  rows={3}
                  value={consultationForm.clinical_notes}
                  disabled={readOnly}
                  onChange={(e) => setConsultationForm({ ...consultationForm, clinical_notes: e.target.value })}
                />
              </label>

              <div className="doctor-form-grid">
                <label className="inline-label">
                  Follow-up
                  <select
                    value={followUpChoice ?? ''}
                    disabled={readOnly}
                    onChange={(e) => setFollowUpChoice(e.target.value || null)}
                  >
                    <option value="">No follow-up</option>
                    {FOLLOW_UP_OPTIONS.filter((o) => o.days !== null).map((o) => (
                      <option key={o.label} value={o.label}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>
                {followUpChoice === 'custom' && (
                  <label className="inline-label">
                    Follow-up date
                    <input
                      type="date"
                      value={followUpCustomDate}
                      disabled={readOnly}
                      onChange={(e) => setFollowUpCustomDate(e.target.value)}
                    />
                  </label>
                )}
              </div>
              {followUpChoice && (
                <label className="inline-label">
                  Follow-up reason
                  <input
                    type="text"
                    value={consultationForm.follow_up_reason}
                    disabled={readOnly}
                    onChange={(e) => setConsultationForm({ ...consultationForm, follow_up_reason: e.target.value })}
                  />
                </label>
              )}

              {!readOnly && (
                <div className="doctor-quick-actions">
                  <button
                    type="button"
                    className="btn-secondary btn"
                    disabled={consultationSaving || completing}
                    onClick={handleSaveConsultation}
                  >
                    {consultationSaving ? 'Saving…' : 'Save draft'}
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={
                      completing ||
                      consultationSaving ||
                      !consultationForm.chief_complaint.trim() ||
                      !consultationForm.diagnosis.trim()
                    }
                    onClick={handleCompleteConsultation}
                    title={
                      !consultationForm.chief_complaint.trim() || !consultationForm.diagnosis.trim()
                        ? 'Chief complaint and diagnosis are required to complete the consultation'
                        : undefined
                    }
                  >
                    {completing ? 'Completing…' : 'Complete consultation'}
                  </button>
                  {consultationSavedAt !== null && !consultationSaving && (
                    <span className="muted">
                      <CheckCircle size={14} weight="fill" /> Draft saved
                    </span>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
