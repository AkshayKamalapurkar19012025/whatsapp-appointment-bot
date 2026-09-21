import { Fragment, useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle, Warning } from '@phosphor-icons/react'
import {
  ApiError,
  cancelOrder,
  completeConsultation,
  createOrder,
  getEncounterSummary,
  getLatestVitals,
  getOrCreateConsultation,
  listOrders,
  recordOrderResult,
  recordVitals,
  saveConsultationDraft,
} from '../api'
import type {
  ClinicalOrder,
  Consultation,
  EncounterSummary,
  OrderPriority,
  OrderResultItemInput,
  OrderType,
  Vitals,
  VitalsPriority,
} from '../types'
import { formatAgeGender, formatDateTime } from '../format'

type Tab = 'triage' | 'consultation' | 'orders'

const ORDER_TYPE_LABELS: Record<OrderType, string> = {
  LAB: 'Laboratory',
  RADIOLOGY: 'Radiology',
  PROCEDURE: 'Procedure',
  SERVICE: 'Service',
  EXTERNAL_REFERRAL: 'External referral',
}

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

  const [orders, setOrders] = useState<ClinicalOrder[]>([])
  const [orderType, setOrderType] = useState<OrderType>('LAB')
  const [orderDescription, setOrderDescription] = useState('')
  const [orderIndication, setOrderIndication] = useState('')
  const [orderPriority, setOrderPriority] = useState<OrderPriority>('ROUTINE')
  const [orderDestination, setOrderDestination] = useState('')
  const [orderSaving, setOrderSaving] = useState(false)
  const [orderError, setOrderError] = useState<string | null>(null)
  // The one order currently showing its "why cancel" reason input --
  // same one-row-at-a-time pattern QueueSection.tsx uses for its own
  // required-reason action (priority).
  const [cancelTargetId, setCancelTargetId] = useState<number | null>(null)
  const [cancelReason, setCancelReason] = useState('')
  const [cancelling, setCancelling] = useState(false)
  // OPD/HIMS master spec Phase 7 -- the one order currently showing its
  // result-entry form, same one-row-at-a-time pattern as cancelTargetId.
  const [resultTargetId, setResultTargetId] = useState<number | null>(null)
  const [resultItems, setResultItems] = useState<OrderResultItemInput[]>([])
  const [resultSaving, setResultSaving] = useState(false)

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
          listOrders(appointmentId).catch(() => []),
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
        const [vitals, orderList] = result
        if (vitals) {
          setLatestVitals(vitals)
          setVitalsForm(vitalsFormFromRecord(vitals))
        }
        setOrders(orderList)
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

  async function handleCreateOrder() {
    setOrderSaving(true)
    setOrderError(null)
    try {
      const created = await createOrder(appointmentId, {
        order_type: orderType,
        description: orderDescription.trim(),
        clinical_indication: orderIndication.trim() || undefined,
        priority: orderPriority,
        external_destination: orderType === 'EXTERNAL_REFERRAL' ? orderDestination.trim() : undefined,
      })
      setOrders((prev) => [created, ...prev])
      setOrderDescription('')
      setOrderIndication('')
      setOrderDestination('')
      setOrderPriority('ROUTINE')
    } catch (err) {
      setOrderError(err instanceof ApiError ? err.message : 'Could not create the order')
    } finally {
      setOrderSaving(false)
    }
  }

  async function handleCancelOrder(orderId: number) {
    if (!cancelReason.trim()) return
    setCancelling(true)
    setOrderError(null)
    try {
      const updated = await cancelOrder(appointmentId, orderId, cancelReason.trim())
      setOrders((prev) => prev.map((o) => (o.id === orderId ? updated : o)))
      setCancelTargetId(null)
      setCancelReason('')
    } catch (err) {
      setOrderError(err instanceof ApiError ? err.message : 'Could not cancel this order')
    } finally {
      setCancelling(false)
    }
  }

  function blankResultItem(): OrderResultItemInput {
    return { parameter: '', result_value: '', unit: '', reference_range: '', is_abnormal: false, is_critical: false }
  }

  function startRecordResult(orderId: number) {
    setResultTargetId(orderId)
    setResultItems([blankResultItem()])
  }

  function cancelRecordResult() {
    setResultTargetId(null)
    setResultItems([])
  }

  function updateResultItem(index: number, patch: Partial<OrderResultItemInput>) {
    setResultItems((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }

  async function handleSaveResult(orderId: number) {
    const items = resultItems
      .filter((item) => item.parameter.trim() && item.result_value.trim())
      .map((item) => ({
        ...item,
        unit: item.unit?.trim() || undefined,
        reference_range: item.reference_range?.trim() || undefined,
      }))
    if (items.length === 0) return

    setResultSaving(true)
    setOrderError(null)
    try {
      const updated = await recordOrderResult(appointmentId, orderId, items)
      setOrders((prev) => prev.map((o) => (o.id === orderId ? updated : o)))
      setResultTargetId(null)
      setResultItems([])
    } catch (err) {
      setOrderError(err instanceof ApiError ? err.message : 'Could not record the result')
    } finally {
      setResultSaving(false)
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
            <button
              type="button"
              className={tab === 'orders' ? 'tab active' : 'tab'}
              onClick={() => setTab('orders')}
            >
              Orders{orders.length > 0 ? ` (${orders.length})` : ''}
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

          {tab === 'orders' && (
            <div className="detail-section">
              {orderError && <p className="error">{orderError}</p>}

              {!readOnly && (
                <div className="doctor-form-grid">
                  <label className="inline-label">
                    Order type
                    <select value={orderType} onChange={(e) => setOrderType(e.target.value as OrderType)}>
                      {(Object.keys(ORDER_TYPE_LABELS) as OrderType[]).map((t) => (
                        <option key={t} value={t}>
                          {ORDER_TYPE_LABELS[t]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="inline-label">
                    Priority
                    <select
                      value={orderPriority}
                      onChange={(e) => setOrderPriority(e.target.value as OrderPriority)}
                    >
                      <option value="ROUTINE">Routine</option>
                      <option value="URGENT">Urgent</option>
                      <option value="STAT">Stat</option>
                    </select>
                  </label>
                  <label className="inline-label doctor-form-full">
                    {orderType === 'EXTERNAL_REFERRAL' ? 'Test / procedure' : 'Test / service / procedure'}
                    <input
                      type="text"
                      value={orderDescription}
                      onChange={(e) => setOrderDescription(e.target.value)}
                      placeholder="e.g. CBC, Chest X-ray, ECG"
                    />
                  </label>
                  {orderType === 'EXTERNAL_REFERRAL' && (
                    <label className="inline-label doctor-form-full">
                      Destination *
                      <input
                        type="text"
                        value={orderDestination}
                        onChange={(e) => setOrderDestination(e.target.value)}
                        placeholder="e.g. City Imaging Center"
                      />
                    </label>
                  )}
                  <label className="inline-label doctor-form-full">
                    Clinical indication
                    <input
                      type="text"
                      value={orderIndication}
                      onChange={(e) => setOrderIndication(e.target.value)}
                    />
                  </label>
                  <div className="doctor-form-full">
                    <button
                      type="button"
                      className="btn"
                      disabled={
                        orderSaving ||
                        !orderDescription.trim() ||
                        (orderType === 'EXTERNAL_REFERRAL' && !orderDestination.trim())
                      }
                      onClick={handleCreateOrder}
                    >
                      {orderSaving ? 'Adding…' : 'Add order'}
                    </button>
                  </div>
                </div>
              )}

              {orders.length === 0 && <p className="muted">No orders yet for this visit.</p>}

              {orders.length > 0 && (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>Description</th>
                      <th>Priority</th>
                      <th>Status</th>
                      <th>Ordered</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((order) => {
                      const actionable = order.status === 'ORDERED' || order.status === 'IN_PROGRESS'
                      return (
                        <Fragment key={order.id}>
                          <tr>
                            <td>{ORDER_TYPE_LABELS[order.order_type]}</td>
                            <td>
                              {order.description}
                              {order.order_type === 'EXTERNAL_REFERRAL' && order.external_destination && (
                                <div className="muted">to {order.external_destination}</div>
                              )}
                              {order.status === 'CANCELLED' && order.cancel_reason && (
                                <div className="muted">Cancelled: {order.cancel_reason}</div>
                              )}
                            </td>
                            <td>{order.priority}</td>
                            <td>
                              <span className={`pill status-${order.status.toLowerCase()}`}>{order.status}</span>
                            </td>
                            <td>{formatDateTime(order.ordered_at)}</td>
                            <td>
                              {actionable &&
                                (cancelTargetId === order.id ? (
                                  <div className="queue-priority-form">
                                    <input
                                      type="text"
                                      placeholder="Reason (required)"
                                      value={cancelReason}
                                      onChange={(e) => setCancelReason(e.target.value)}
                                      autoFocus
                                    />
                                    <button
                                      type="button"
                                      className="btn btn-sm"
                                      disabled={!cancelReason.trim() || cancelling}
                                      onClick={() => handleCancelOrder(order.id)}
                                    >
                                      {cancelling ? 'Cancelling…' : 'Confirm'}
                                    </button>
                                    <button
                                      type="button"
                                      className="btn-secondary btn btn-sm"
                                      onClick={() => {
                                        setCancelTargetId(null)
                                        setCancelReason('')
                                      }}
                                    >
                                      Back
                                    </button>
                                  </div>
                                ) : resultTargetId === order.id ? null : (
                                  <div className="queue-row-actions">
                                    <button
                                      type="button"
                                      className="btn btn-sm"
                                      onClick={() => startRecordResult(order.id)}
                                    >
                                      Record result
                                    </button>
                                    <button
                                      type="button"
                                      className="btn-danger btn btn-sm"
                                      onClick={() => {
                                        setCancelTargetId(order.id)
                                        setCancelReason('')
                                      }}
                                    >
                                      Cancel
                                    </button>
                                  </div>
                                ))}
                            </td>
                          </tr>

                          {resultTargetId === order.id && (
                            <tr>
                              <td colSpan={6}>
                                {resultItems.map((item, index) => (
                                  <div key={index} className="doctor-form-grid">
                                    <label className="inline-label">
                                      Parameter
                                      <input
                                        type="text"
                                        value={item.parameter}
                                        placeholder="e.g. Hemoglobin, Findings"
                                        onChange={(e) => updateResultItem(index, { parameter: e.target.value })}
                                      />
                                    </label>
                                    <label className="inline-label">
                                      Result
                                      <input
                                        type="text"
                                        value={item.result_value}
                                        onChange={(e) => updateResultItem(index, { result_value: e.target.value })}
                                      />
                                    </label>
                                    <label className="inline-label">
                                      Unit
                                      <input
                                        type="text"
                                        value={item.unit ?? ''}
                                        onChange={(e) => updateResultItem(index, { unit: e.target.value })}
                                      />
                                    </label>
                                    <label className="inline-label">
                                      Reference range
                                      <input
                                        type="text"
                                        value={item.reference_range ?? ''}
                                        onChange={(e) =>
                                          updateResultItem(index, { reference_range: e.target.value })
                                        }
                                      />
                                    </label>
                                    <label className="inline-label checkbox-label">
                                      <input
                                        type="checkbox"
                                        checked={item.is_abnormal ?? false}
                                        onChange={(e) => updateResultItem(index, { is_abnormal: e.target.checked })}
                                      />
                                      Abnormal
                                    </label>
                                    <label className="inline-label checkbox-label">
                                      <input
                                        type="checkbox"
                                        checked={item.is_critical ?? false}
                                        onChange={(e) => updateResultItem(index, { is_critical: e.target.checked })}
                                      />
                                      Critical
                                    </label>
                                  </div>
                                ))}
                                <div className="doctor-quick-actions">
                                  <button
                                    type="button"
                                    className="btn-secondary btn btn-sm"
                                    onClick={() => setResultItems((prev) => [...prev, blankResultItem()])}
                                  >
                                    + Add parameter
                                  </button>
                                  <button
                                    type="button"
                                    className="btn btn-sm"
                                    disabled={
                                      resultSaving ||
                                      !resultItems.some((i) => i.parameter.trim() && i.result_value.trim())
                                    }
                                    onClick={() => handleSaveResult(order.id)}
                                  >
                                    {resultSaving ? 'Saving…' : 'Save result'}
                                  </button>
                                  <button type="button" className="btn-secondary btn btn-sm" onClick={cancelRecordResult}>
                                    Cancel
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}

                          {order.results.length > 0 && (
                            <tr>
                              <td colSpan={6}>
                                <table className="data-table">
                                  <thead>
                                    <tr>
                                      <th>Parameter</th>
                                      <th>Result</th>
                                      <th>Unit</th>
                                      <th>Reference range</th>
                                      <th></th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {order.results.map((r) => (
                                      <tr key={r.id}>
                                        <td>{r.parameter}</td>
                                        <td>{r.result_value}</td>
                                        <td>{r.unit ?? ''}</td>
                                        <td>{r.reference_range ?? ''}</td>
                                        <td>
                                          {r.is_critical && <span className="pill status-cancelled">Critical</span>}
                                          {!r.is_critical && r.is_abnormal && (
                                            <span className="pill status-pending">Abnormal</span>
                                          )}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
