import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle, Clock, Warning } from '@phosphor-icons/react'
import {
  ApiError,
  addPatientAllergy,
  amendConsultation,
  cancelOrder,
  completeConsultation,
  createOrder,
  getConsultationAmendments,
  getEncounterSummary,
  getLatestVitals,
  getOrCreateConsultation,
  getPatientAllergies,
  getPatientTimeline,
  listOrders,
  recordOrderResult,
  recordVitals,
  resolvePatientAllergy,
  saveConsultationDraft,
} from '../api'
import type {
  AllergySeverity,
  ClinicalOrder,
  Consultation,
  ConsultationAmendment,
  ConsultationDisposition,
  EncounterSummary,
  OrderPriority,
  OrderResultItemInput,
  OrderType,
  PatientAllergy,
  PatientTimeline,
  TimelineVitals,
  Vitals,
  VitalsPriority,
} from '../types'
import { formatAgeGender, formatDateTime, formatTime } from '../format'
import PrescriptionPanel from './PrescriptionPanel'
import AppointmentBillingPanel from './AppointmentBillingPanel'

type Tab = 'consultation' | 'triage' | 'orders' | 'prescription' | 'billing'

const ORDER_TYPE_LABELS: Record<OrderType, string> = {
  LAB: 'Laboratory',
  RADIOLOGY: 'Radiology',
  PROCEDURE: 'Procedure',
  SERVICE: 'Service',
  EXTERNAL_REFERRAL: 'External referral',
}

// The full lab/radiology lifecycle (migrations/0054_diagnostic_
// workflow.sql), in order -- used to draw the stage bar in the Orders
// tab below. Every other order_type (PROCEDURE/SERVICE/EXTERNAL_
// REFERRAL) never leaves ORDERED/IN_PROGRESS/COMPLETED/CANCELLED, so
// they get a plain status pill instead, not this bar.
const LAB_RADIOLOGY_STAGES: { status: ClinicalOrder['status']; label: string }[] = [
  { status: 'ORDERED', label: 'Ordered' },
  { status: 'COLLECTED', label: 'Collected' },
  { status: 'RESULT_ENTERED', label: 'Result' },
  { status: 'VERIFIED', label: 'Verified' },
  { status: 'COMPLETED', label: 'Released' },
]

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
  // The one optional coded-diagnosis slot (migrations/0056_
  // consultation_diagnosis_coding.sql) -- singular columns on
  // consultations, not a child table, so this is genuinely one
  // code/system/display triple, never a list. There is no ICD-10
  // lookup/search endpoint anywhere in this app (grepped app/api and
  // app/services -- none exists), so this is honest manual structured
  // entry against the real columns, not a fake searchable index.
  diagnosis_code_system: string
  diagnosis_code: string
  diagnosis_code_display: string
  clinical_notes: string
  follow_up_reason: string
  disposition: ConsultationDisposition | ''
  disposition_notes: string
}

function consultationFormFromRecord(c: Consultation): ConsultationFormState {
  return {
    chief_complaint: c.chief_complaint ?? '',
    history_notes: c.history_notes ?? '',
    examination_notes: c.examination_notes ?? '',
    diagnosis: c.diagnosis ?? '',
    diagnosis_code_system: c.diagnosis_code_system ?? '',
    diagnosis_code: c.diagnosis_code ?? '',
    diagnosis_code_display: c.diagnosis_code_display ?? '',
    clinical_notes: c.clinical_notes ?? '',
    follow_up_reason: c.follow_up_reason ?? '',
    disposition: c.disposition ?? '',
    disposition_notes: c.disposition_notes ?? '',
  }
}

// ADMIT_TO_IPD deliberately excluded -- no IPD module exists yet
// (types.ts's own Consultation.disposition comment calls it "a stub,
// no IPD/referral workflow behind it yet"). The backend enum value
// itself is untouched (existing historical rows keep reading fine);
// this UI simply never offers selecting it going forward.
const DISPOSITION_LABELS: Record<Exclude<ConsultationDisposition, 'ADMIT_TO_IPD'>, string> = {
  FOLLOW_UP: 'Follow-up',
  REFER: 'Refer',
  EMERGENCY: 'Emergency',
}

function vitalsBpTrend(recent: TimelineVitals[]): 'up' | 'down' | 'flat' | null {
  const withBp = recent.filter((v) => v.bp_systolic !== null)
  if (withBp.length < 2) return null
  const [latest, previous] = withBp
  if (latest.bp_systolic === previous.bp_systolic) return 'flat'
  return (latest.bp_systolic ?? 0) > (previous.bp_systolic ?? 0) ? 'up' : 'down'
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
  canAmendConsultation,
  canManageBilling,
  canRecordVitals,
  canWriteConsultation,
  canCreateOrders,
  canCreatePrescriptions,
  onBack,
  onOpenLabWorklist,
}: {
  appointmentId: number
  // Six distinct capabilities, not one -- each server-enforced by its
  // own permission (migrations/0043_role_based_access.sql,
  // migrations/0048_clinical_rbac_permissions.sql). A DOCTOR who can
  // amend their own consultation notes has no business voiding a
  // charge, and a BILLING account managing this visit's bill has no
  // business amending clinical notes -- collapsing these into fewer
  // booleans would grant one role another's capability by accident.
  canAmendConsultation: boolean
  canManageBilling: boolean
  canRecordVitals: boolean
  canWriteConsultation: boolean
  canCreateOrders: boolean
  canCreatePrescriptions: boolean
  onBack: () => void
  // Real navigation to the Lab/Radiology Worklist section (AdminApp.tsx),
  // not this component's own onBack (which returns to the queue) --
  // DOCTOR's own ROLE_VISIBLE_SECTIONS doesn't include 'lab-worklist'
  // (that sidebar entry is LAB_TECH's own, plus ADMIN/STAFF who see
  // every section), so this link is a real but role-limited shortcut,
  // same as any other direct goTo call bypassing the sidebar -- the
  // worklist's own actions stay individually permission-gated
  // server-side regardless of how staff got there.
  onOpenLabWorklist: () => void
}) {
  // Notes is the landing tab (the mockup's own default) -- vitals/
  // allergies are now always visible in the left rail instead of
  // needing their own primary tab first.
  const [tab, setTab] = useState<Tab>('consultation')
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

  // Cross-visit history (GET /patients/{id}/timeline) -- the one
  // source for both the left rail's vitals trend (more than just this
  // encounter's own latest reading, which getLatestVitals alone can
  // never show) and its "Recent visits" list. Fetched once the
  // encounter's patient_id is known; failure is non-fatal (the rest of
  // the workspace still works from getLatestVitals/allergies alone).
  const [timeline, setTimeline] = useState<PatientTimeline | null>(null)

  // Allergy list (master spec section 91's clinical-safety warning).
  const [allergies, setAllergies] = useState<PatientAllergy[]>([])
  const [allergyError, setAllergyError] = useState<string | null>(null)
  const [showAllergyForm, setShowAllergyForm] = useState(false)
  const [allergenInput, setAllergenInput] = useState('')
  const [reactionInput, setReactionInput] = useState('')
  const [severityInput, setSeverityInput] = useState<AllergySeverity | ''>('')
  const [allergySaving, setAllergySaving] = useState(false)
  // One-row-at-a-time reason input, same pattern as cancelTargetId below.
  const [resolveTargetId, setResolveTargetId] = useState<number | null>(null)
  const [resolveReason, setResolveReason] = useState('')
  const [resolving, setResolving] = useState(false)

  const [consultation, setConsultation] = useState<Consultation | null>(null)
  const [consultationForm, setConsultationForm] = useState<ConsultationFormState>(
    consultationFormFromRecord({} as Consultation),
  )
  const [followUpChoice, setFollowUpChoice] = useState<string | null>(null)
  const [followUpCustomDate, setFollowUpCustomDate] = useState('')
  // Draft inputs for the one coded-diagnosis slot's manual entry row
  // (see ConsultationFormState's own diagnosis_code comment) -- kept
  // separate from consultationForm itself so typing a code doesn't
  // commit it until "Add" is pressed.
  const [diagnosisCodeDraft, setDiagnosisCodeDraft] = useState('')
  const [diagnosisDisplayDraft, setDiagnosisDisplayDraft] = useState('')
  const [consultationSaving, setConsultationSaving] = useState(false)
  const [consultationError, setConsultationError] = useState<string | null>(null)
  const [consultationSavedAt, setConsultationSavedAt] = useState<number | null>(null)
  const [completing, setCompleting] = useState(false)
  const [completeError, setCompleteError] = useState<string | null>(null)

  // Amendment (master spec section 70) -- correcting a COMPLETED
  // consultation. `amending` locally re-enables the consultation tab's
  // fields without touching the global `readOnly` (vitals/orders stay
  // exactly as read-only as the visit's own state says). There is no
  // time-based grace window on either side of this: clinical_
  // services.py locks the moment status flips to COMPLETED (no
  // timedelta/minutes check anywhere in that file), so the UI doesn't
  // pretend one exists either -- Amend is the only way back in, at any
  // time after completion, same as today.
  const [amending, setAmending] = useState(false)
  const [amendReason, setAmendReason] = useState('')
  const [amendSaving, setAmendSaving] = useState(false)
  const [amendError, setAmendError] = useState<string | null>(null)
  const [amendments, setAmendments] = useState<ConsultationAmendment[]>([])
  const [showAmendHistory, setShowAmendHistory] = useState(false)

  const [orders, setOrders] = useState<ClinicalOrder[]>([])
  const [orderType, setOrderType] = useState<OrderType>('LAB')
  const [orderDescription, setOrderDescription] = useState('')
  const [orderIndication, setOrderIndication] = useState('')
  const [orderPriority, setOrderPriority] = useState<OrderPriority>('ROUTINE')
  const [orderDestination, setOrderDestination] = useState('')
  const [orderSaving, setOrderSaving] = useState(false)
  const [orderError, setOrderError] = useState<string | null>(null)
  const [showNewOrderForm, setShowNewOrderForm] = useState(false)
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
  // Master spec section 54's gap #6: lab/radiology orders had no
  // requisition print view. Only one order's print-only layout exists
  // in the DOM at a time (see the useEffect below) -- printing whatever
  // *every* LAB/RADIOLOGY row's own print-area would otherwise put on
  // the page isn't what "Print requisition" on one row means.
  const [printOrderTarget, setPrintOrderTarget] = useState<ClinicalOrder | null>(null)

  // Re-renders the banner's "N min" elapsed readout once a minute --
  // not every second (this isn't a stopwatch), matching the app's own
  // 30s/20s polling cadence elsewhere for "keep roughly fresh" data.
  const [, forceTick] = useState(0)
  useEffect(() => {
    const interval = setInterval(() => forceTick((n) => n + 1), 60_000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (printOrderTarget) {
      window.print()
    }
  }, [printOrderTarget])

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
          getPatientAllergies(summary.patient_id).catch(() => []),
          getPatientTimeline(summary.patient_id).catch(() => null),
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
        const [vitals, orderList, allergyList, patientTimeline] = result
        if (vitals) {
          setLatestVitals(vitals)
          setVitalsForm(vitalsFormFromRecord(vitals))
        }
        setOrders(orderList)
        setAllergies(allergyList)
        setTimeline(patientTimeline)
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

  useEffect(() => {
    if (consultation?.status !== 'COMPLETED') return
    let cancelled = false
    getConsultationAmendments(appointmentId)
      .then((list) => {
        if (!cancelled) setAmendments(list)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [appointmentId, consultation?.status])

  function startAmend() {
    if (!consultation) return
    setConsultationForm(consultationFormFromRecord(consultation))
    setFollowUpChoice(consultation.follow_up_date ? 'custom' : null)
    setFollowUpCustomDate(consultation.follow_up_date ?? '')
    setAmendReason('')
    setAmendError(null)
    setAmending(true)
  }

  async function handleSaveAmendment() {
    setAmendSaving(true)
    setAmendError(null)
    try {
      const saved = await amendConsultation(appointmentId, {
        reason: amendReason.trim(),
        chief_complaint: consultationForm.chief_complaint.trim() || undefined,
        history_notes: consultationForm.history_notes.trim() || undefined,
        examination_notes: consultationForm.examination_notes.trim() || undefined,
        diagnosis: consultationForm.diagnosis.trim() || undefined,
        diagnosis_code_system: consultationForm.diagnosis_code.trim()
          ? consultationForm.diagnosis_code_system.trim() || 'ICD-10'
          : undefined,
        diagnosis_code: consultationForm.diagnosis_code.trim() || undefined,
        diagnosis_code_display: consultationForm.diagnosis_code_display.trim() || undefined,
        clinical_notes: consultationForm.clinical_notes.trim() || undefined,
        follow_up_date: currentFollowUpDate(),
        follow_up_reason: consultationForm.follow_up_reason.trim() || undefined,
        disposition: consultationForm.disposition || undefined,
        disposition_notes: consultationForm.disposition_notes.trim() || undefined,
      })
      setConsultation(saved)
      setAmending(false)
      const history = await getConsultationAmendments(appointmentId).catch(() => amendments)
      setAmendments(history)
    } catch (err) {
      setAmendError(err instanceof ApiError ? err.message : 'Could not save this amendment')
    } finally {
      setAmendSaving(false)
    }
  }

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
      // The trend card should reflect a just-recorded reading without
      // needing a full page reload -- getPatientTimeline again is the
      // one source of truth it reads from, so re-fetch it rather than
      // hand-splicing `saved` into the cached timeline shape.
      if (encounter) {
        getPatientTimeline(encounter.patient_id)
          .then(setTimeline)
          .catch(() => undefined)
      }
    } catch (err) {
      setVitalsError(err instanceof ApiError ? err.message : 'Could not save vitals')
    } finally {
      setVitalsSaving(false)
    }
  }

  async function handleAddAllergy() {
    if (!encounter || !allergenInput.trim()) return
    setAllergySaving(true)
    setAllergyError(null)
    try {
      const created = await addPatientAllergy(encounter.patient_id, {
        allergen: allergenInput.trim(),
        reaction: reactionInput.trim() || undefined,
        severity: severityInput || undefined,
      })
      setAllergies((prev) => [created, ...prev])
      setAllergenInput('')
      setReactionInput('')
      setSeverityInput('')
      setShowAllergyForm(false)
    } catch (err) {
      setAllergyError(err instanceof ApiError ? err.message : 'Could not add this allergy')
    } finally {
      setAllergySaving(false)
    }
  }

  async function handleResolveAllergy(allergyId: number) {
    if (!encounter || !resolveReason.trim()) return
    setResolving(true)
    setAllergyError(null)
    try {
      await resolvePatientAllergy(encounter.patient_id, allergyId, resolveReason.trim())
      setAllergies((prev) => prev.filter((a) => a.id !== allergyId))
      setResolveTargetId(null)
      setResolveReason('')
    } catch (err) {
      setAllergyError(err instanceof ApiError ? err.message : 'Could not resolve this allergy')
    } finally {
      setResolving(false)
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
        diagnosis_code_system: consultationForm.diagnosis_code.trim()
          ? consultationForm.diagnosis_code_system.trim() || 'ICD-10'
          : undefined,
        diagnosis_code: consultationForm.diagnosis_code.trim() || undefined,
        diagnosis_code_display: consultationForm.diagnosis_code_display.trim() || undefined,
        clinical_notes: consultationForm.clinical_notes.trim() || undefined,
        follow_up_date: currentFollowUpDate(),
        follow_up_reason: consultationForm.follow_up_reason.trim() || undefined,
        disposition: consultationForm.disposition || undefined,
        disposition_notes: consultationForm.disposition_notes.trim() || undefined,
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
        diagnosis_code_system: consultationForm.diagnosis_code.trim()
          ? consultationForm.diagnosis_code_system.trim() || 'ICD-10'
          : undefined,
        diagnosis_code: consultationForm.diagnosis_code.trim() || undefined,
        diagnosis_code_display: consultationForm.diagnosis_code_display.trim() || undefined,
        clinical_notes: consultationForm.clinical_notes.trim() || undefined,
        follow_up_date: currentFollowUpDate(),
        follow_up_reason: consultationForm.follow_up_reason.trim() || undefined,
        disposition: consultationForm.disposition || undefined,
        disposition_notes: consultationForm.disposition_notes.trim() || undefined,
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
      setShowNewOrderForm(false)
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

  // GET /patients/{id}/allergies defaults include_resolved to False
  // (list_patient_allergies_service) with no query param exposed to
  // change that, so `allergies` here is always the active set already
  // -- never a mix that needs its own active/resolved filter.
  const activeAllergies = allergies

  // Every vitals reading across every past encounter for this patient,
  // most recent first -- getPatientTimeline's own visits are already
  // ordered newest-encounter-first (patient_timeline_service.py), but
  // each visit's own vitals list is oldest-first internally, so this
  // flattens and re-sorts by recorded_at directly rather than trusting
  // that nesting order.
  const vitalsHistory: TimelineVitals[] = (timeline?.visits ?? [])
    .flatMap((v) => v.vitals)
    .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at))
    .slice(0, 5)
  const bpTrend = vitalsBpTrend(vitalsHistory)
  const previousVitals = vitalsHistory[1] ?? null

  const elapsedMinutes = encounter ? Math.max(0, Math.round((Date.now() - new Date(encounter.opened_at).getTime()) / 60000)) : null

  const recentVisits = (timeline?.visits ?? []).filter((v) => v.encounter_id !== encounter?.encounter_id).slice(0, 4)

  return (
    <section className="consult-workspace">
      <button type="button" className="link doctor-workspace-back" onClick={onBack}>
        <ArrowLeft size={16} weight="bold" /> Back to queue
      </button>

      {encounter && (
        <header className="consult-banner">
          <div className="consult-banner-identity">
            <span className="consult-banner-avatar" aria-hidden="true">
              {encounter.patient_name
                .split(' ')
                .map((p) => p[0])
                .slice(0, 2)
                .join('')
                .toUpperCase()}
            </span>
            <div className="consult-banner-text">
              <div className="consult-banner-name-row">
                <h2>{encounter.patient_name}</h2>
                {encounter.token_number !== null && <span className="pill consult-token-pill">Token {encounter.token_number}</span>}
              </div>
              <div className="consult-banner-meta">
                <span>UHID {encounter.patient_uhid}</span>
                {formatAgeGender(encounter.patient_date_of_birth, encounter.patient_gender) && (
                  <span>{formatAgeGender(encounter.patient_date_of_birth, encounter.patient_gender)}</span>
                )}
                <span>{encounter.doctor_name}</span>
              </div>
            </div>
            {activeAllergies.length > 0 && (
              <div className="consult-banner-allergy-flag">
                <Warning size={15} weight="fill" aria-hidden="true" />
                {activeAllergies.length} active allerg{activeAllergies.length === 1 ? 'y' : 'ies'}
              </div>
            )}
          </div>
          <div className="consult-banner-actions">
            {elapsedMinutes !== null && (
              <span className="muted consult-banner-elapsed">
                <Clock size={14} weight="bold" aria-hidden="true" /> Started {formatTime(encounter.opened_at)} · {elapsedMinutes} min
              </span>
            )}
            {!readOnly && canWriteConsultation && (
              <button type="button" className="btn-secondary btn btn-sm" disabled={consultationSaving} onClick={handleSaveConsultation}>
                {consultationSaving ? 'Saving…' : 'Save draft'}
              </button>
            )}
            {!readOnly && canWriteConsultation && (
              <button
                type="button"
                className="btn btn-sm"
                disabled={completing || !consultationForm.chief_complaint.trim() || !consultationForm.diagnosis.trim()}
                onClick={handleCompleteConsultation}
                title={
                  !consultationForm.chief_complaint.trim() || !consultationForm.diagnosis.trim()
                    ? 'Chief complaint and diagnosis are required to complete the consultation'
                    : undefined
                }
              >
                {completing ? 'Completing…' : 'Complete visit'}
              </button>
            )}
          </div>
        </header>
      )}

      {notCheckedIn && (
        <>
          <div className="state-block">
            <Warning size={20} weight="regular" />
            This patient isn&apos;t currently checked in. Triage and consultation become available once they&apos;ve
            checked in and a queue token has been issued. Billing doesn&apos;t require check-in, so it&apos;s
            available below.
          </div>
          <h4>Billing</h4>
          <AppointmentBillingPanel
            appointmentId={appointmentId}
            canManageBilling={canManageBilling}
            patientName={encounter?.patient_name ?? ''}
            patientUhid={encounter?.patient_uhid ?? ''}
            doctorName={encounter?.doctor_name ?? ''}
          />
        </>
      )}

      {!notCheckedIn && (
        <>
          <div className="tabs">
            <button
              type="button"
              className={tab === 'consultation' ? 'tab active consult-tab-notes' : 'tab consult-tab-notes'}
              onClick={() => setTab('consultation')}
            >
              Notes
            </button>
            <button
              type="button"
              className={tab === 'triage' ? 'tab active consult-tab-vitals' : 'tab consult-tab-vitals'}
              onClick={() => setTab('triage')}
            >
              Vitals &amp; triage
            </button>
            <button
              type="button"
              className={tab === 'orders' ? 'tab active consult-tab-orders' : 'tab consult-tab-orders'}
              onClick={() => setTab('orders')}
            >
              Orders{orders.length > 0 ? ` (${orders.length})` : ''}
            </button>
            <button
              type="button"
              className={tab === 'prescription' ? 'tab active consult-tab-prescription' : 'tab consult-tab-prescription'}
              onClick={() => setTab('prescription')}
            >
              Prescription
            </button>
            <button
              type="button"
              className={tab === 'billing' ? 'tab active consult-tab-billing' : 'tab consult-tab-billing'}
              onClick={() => setTab('billing')}
            >
              Billing
            </button>
          </div>

          {tab === 'consultation' && (
            <div className="consult-notes-layout">
              <div className="consult-left-rail">
                <section className="consult-vitals-card">
                  <div className="consult-card-heading">
                    <h3>Vitals</h3>
                  </div>
                  {latestVitals ? (
                    <>
                      <div className="consult-vitals-grid">
                        {latestVitals.bp_systolic !== null && (
                          <div className="consult-vitals-tile">
                            <span className="muted">BP</span>
                            <strong>
                              {latestVitals.bp_systolic}/{latestVitals.bp_diastolic}
                            </strong>
                            {bpTrend && previousVitals && (
                              <span className={`consult-vitals-delta consult-vitals-delta-${bpTrend}`}>
                                {bpTrend === 'up' ? '↑' : bpTrend === 'down' ? '↓' : '→'} from {previousVitals.bp_systolic}/
                                {previousVitals.bp_diastolic}
                              </span>
                            )}
                          </div>
                        )}
                        {latestVitals.pulse !== null && (
                          <div className="consult-vitals-tile">
                            <span className="muted">Pulse</span>
                            <strong>
                              {latestVitals.pulse} <span className="muted">bpm</span>
                            </strong>
                          </div>
                        )}
                        {latestVitals.temperature_celsius !== null && (
                          <div className="consult-vitals-tile">
                            <span className="muted">Temp</span>
                            <strong>
                              {latestVitals.temperature_celsius} <span className="muted">°C</span>
                            </strong>
                          </div>
                        )}
                        {latestVitals.spo2 !== null && (
                          <div className="consult-vitals-tile">
                            <span className="muted">SpO₂</span>
                            <strong>
                              {latestVitals.spo2} <span className="muted">%</span>
                            </strong>
                          </div>
                        )}
                      </div>
                      <p className="muted consult-vitals-footnote">
                        Taken {formatDateTime(latestVitals.recorded_at)}
                        {vitalsHistory.length > 1 ? ` · last ${vitalsHistory.length} readings shown` : ''}
                      </p>
                    </>
                  ) : (
                    <p className="muted">No vitals recorded yet.</p>
                  )}
                </section>

                <section className="consult-allergy-card">
                  <div className="consult-card-heading">
                    <h3>Allergies</h3>
                    {!showAllergyForm && (
                      <button type="button" className="link-btn" onClick={() => setShowAllergyForm(true)}>
                        + Add
                      </button>
                    )}
                  </div>
                  {allergyError && <p className="error">{allergyError}</p>}
                  {activeAllergies.length === 0 && !showAllergyForm && <p className="muted">No known allergies recorded.</p>}
                  {activeAllergies.length > 0 && (
                    <ul className="consult-allergy-list">
                      {activeAllergies.map((a) => (
                        <li
                          key={a.id}
                          className={a.severity === 'SEVERE' ? 'consult-allergy-entry consult-allergy-entry-severe' : 'consult-allergy-entry'}
                        >
                          <div className="consult-allergy-entry-head">
                            <strong>{a.allergen}</strong>
                            {a.severity && <span className={`pill severity-${a.severity.toLowerCase()}`}>{a.severity}</span>}
                          </div>
                          {(a.reaction || a.recorded_at) && (
                            <div className="muted">
                              {[a.reaction, a.recorded_at ? `noted ${new Date(a.recorded_at).getFullYear()}` : null]
                                .filter(Boolean)
                                .join(' · ')}
                            </div>
                          )}
                          {resolveTargetId === a.id ? (
                            <span className="inline-form">
                              <input
                                type="text"
                                placeholder="Reason for removing"
                                value={resolveReason}
                                onChange={(e) => setResolveReason(e.target.value)}
                              />
                              <button
                                type="button"
                                className="btn btn-sm"
                                disabled={resolving || !resolveReason.trim()}
                                onClick={() => handleResolveAllergy(a.id)}
                              >
                                {resolving ? 'Removing…' : 'Confirm'}
                              </button>
                              <button
                                type="button"
                                className="btn-secondary btn btn-sm"
                                onClick={() => {
                                  setResolveTargetId(null)
                                  setResolveReason('')
                                }}
                              >
                                Cancel
                              </button>
                            </span>
                          ) : (
                            <button
                              type="button"
                              className="link"
                              onClick={() => {
                                setResolveTargetId(a.id)
                                setResolveReason('')
                              }}
                            >
                              Remove
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}

                  {showAllergyForm && (
                    <div className="doctor-form-grid">
                      <label className="inline-label">
                        Allergen
                        <input
                          type="text"
                          value={allergenInput}
                          onChange={(e) => setAllergenInput(e.target.value)}
                          placeholder="e.g. Penicillin"
                        />
                      </label>
                      <label className="inline-label">
                        Reaction
                        <input
                          type="text"
                          value={reactionInput}
                          onChange={(e) => setReactionInput(e.target.value)}
                          placeholder="e.g. Rash"
                        />
                      </label>
                      <label className="inline-label">
                        Severity
                        <select value={severityInput} onChange={(e) => setSeverityInput(e.target.value as AllergySeverity | '')}>
                          <option value="">—</option>
                          <option value="MILD">Mild</option>
                          <option value="MODERATE">Moderate</option>
                          <option value="SEVERE">Severe</option>
                        </select>
                      </label>
                      <span className="inline-form">
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={allergySaving || !allergenInput.trim()}
                          onClick={handleAddAllergy}
                        >
                          {allergySaving ? 'Saving…' : 'Save allergy'}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary btn btn-sm"
                          onClick={() => {
                            setShowAllergyForm(false)
                            setAllergenInput('')
                            setReactionInput('')
                            setSeverityInput('')
                          }}
                        >
                          Cancel
                        </button>
                      </span>
                    </div>
                  )}

                </section>

                {recentVisits.length > 0 && (
                  <section className="consult-recent-visits-card">
                    <h3>Recent visits</h3>
                    {recentVisits.map((v) => (
                      <div key={v.encounter_id} className="consult-recent-visit">
                        <div>
                          {new Date(v.started_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} · {v.doctor_name}
                        </div>
                        <div className="muted">{v.consultation?.diagnosis ?? v.consultation?.chief_complaint ?? 'No note recorded'}</div>
                      </div>
                    ))}
                  </section>
                )}
              </div>

              <section className="consult-notes-card">
                <div className="consult-card-heading">
                  <h3>Consultation note</h3>
                </div>
                {readOnly && !amending && (
                  <p className="muted">
                    {consultation?.status === 'COMPLETED'
                      ? `Completed ${consultation.completed_at ? formatDateTime(consultation.completed_at) : ''} -- locked immediately on completion, corrections go through Amend below.`
                      : 'This visit is no longer in progress -- the consultation can be viewed but not edited.'}
                  </p>
                )}
                {amending && (
                  <p className="muted">
                    Amending a completed consultation -- every field below is editable, and the previous values
                    will be kept in the amendment history.
                  </p>
                )}
                {consultationError && <p className="error">{consultationError}</p>}
                {completeError && <p className="error">{completeError}</p>}

                <label className="inline-label">
                  Chief complaint *
                  <textarea
                    rows={2}
                    value={consultationForm.chief_complaint}
                    disabled={readOnly && !amending}
                    onChange={(e) => setConsultationForm({ ...consultationForm, chief_complaint: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  History
                  <textarea
                    rows={3}
                    value={consultationForm.history_notes}
                    disabled={readOnly && !amending}
                    onChange={(e) => setConsultationForm({ ...consultationForm, history_notes: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Examination
                  <textarea
                    rows={3}
                    value={consultationForm.examination_notes}
                    disabled={readOnly && !amending}
                    onChange={(e) => setConsultationForm({ ...consultationForm, examination_notes: e.target.value })}
                  />
                </label>

                <div className="consult-diagnosis-row">
                  <label className="inline-label consult-diagnosis-field">
                    Diagnosis *
                    <textarea
                      rows={2}
                      value={consultationForm.diagnosis}
                      disabled={readOnly && !amending}
                      onChange={(e) => setConsultationForm({ ...consultationForm, diagnosis: e.target.value })}
                    />
                  </label>
                  <label className="inline-label" style={{ width: 190 }}>
                    Follow-up
                    <select
                      value={followUpChoice ?? ''}
                      disabled={readOnly && !amending}
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
                </div>
                {followUpChoice === 'custom' && (
                  <label className="inline-label">
                    Follow-up date
                    <input
                      type="date"
                      value={followUpCustomDate}
                      disabled={readOnly && !amending}
                      onChange={(e) => setFollowUpCustomDate(e.target.value)}
                    />
                  </label>
                )}
                {followUpChoice && (
                  <label className="inline-label">
                    Follow-up reason
                    <input
                      type="text"
                      value={consultationForm.follow_up_reason}
                      disabled={readOnly && !amending}
                      onChange={(e) => setConsultationForm({ ...consultationForm, follow_up_reason: e.target.value })}
                    />
                  </label>
                )}

                {/* One coded-diagnosis slot (consultations.diagnosis_code
                    et al, migrations/0056) -- not a searchable multi-
                    chip picker, since no ICD-10 index exists to search
                    and the schema itself only stores a single code.
                    Empty -> a manual code+description entry row; filled
                    -> a removable chip, which clears the slot back to
                    empty rather than "adding a second" one. */}
                <label className="inline-label">
                  ICD-10 code (optional, structured)
                  <div className="consult-diagnosis-chip-row">
                    {consultationForm.diagnosis_code ? (
                      <span className="consult-diagnosis-chip">
                        {consultationForm.diagnosis_code}
                        {consultationForm.diagnosis_code_display ? ` · ${consultationForm.diagnosis_code_display}` : ''}
                        {!(readOnly && !amending) && (
                          <button
                            type="button"
                            aria-label="Remove diagnosis code"
                            onClick={() =>
                              setConsultationForm({
                                ...consultationForm,
                                diagnosis_code: '',
                                diagnosis_code_display: '',
                                diagnosis_code_system: '',
                              })
                            }
                          >
                            ×
                          </button>
                        )}
                      </span>
                    ) : (
                      !(readOnly && !amending) && (
                        <>
                          <input
                            type="text"
                            placeholder="Code, e.g. I10"
                            className="consult-diagnosis-code-input"
                            value={diagnosisCodeDraft}
                            onChange={(e) => setDiagnosisCodeDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key !== 'Enter' || !diagnosisCodeDraft.trim()) return
                              setConsultationForm({
                                ...consultationForm,
                                diagnosis_code: diagnosisCodeDraft.trim(),
                                diagnosis_code_display: diagnosisDisplayDraft.trim(),
                              })
                              setDiagnosisCodeDraft('')
                              setDiagnosisDisplayDraft('')
                            }}
                          />
                          <input
                            type="text"
                            placeholder="Description"
                            value={diagnosisDisplayDraft}
                            onChange={(e) => setDiagnosisDisplayDraft(e.target.value)}
                          />
                          <button
                            type="button"
                            className="btn-secondary btn btn-sm"
                            disabled={!diagnosisCodeDraft.trim()}
                            onClick={() => {
                              setConsultationForm({
                                ...consultationForm,
                                diagnosis_code: diagnosisCodeDraft.trim(),
                                diagnosis_code_display: diagnosisDisplayDraft.trim(),
                              })
                              setDiagnosisCodeDraft('')
                              setDiagnosisDisplayDraft('')
                            }}
                          >
                            Add
                          </button>
                        </>
                      )
                    )}
                  </div>
                </label>

                <label className="inline-label">
                  Clinical notes
                  <textarea
                    rows={3}
                    value={consultationForm.clinical_notes}
                    disabled={readOnly && !amending}
                    onChange={(e) => setConsultationForm({ ...consultationForm, clinical_notes: e.target.value })}
                  />
                </label>

                <label className="inline-label">
                  Disposition
                  <div role="radiogroup" aria-label="Disposition" className="consult-disposition-group">
                    {(Object.keys(DISPOSITION_LABELS) as Exclude<ConsultationDisposition, 'ADMIT_TO_IPD'>[]).map((d) => (
                      <button
                        key={d}
                        type="button"
                        role="radio"
                        aria-checked={consultationForm.disposition === d}
                        className={consultationForm.disposition === d ? 'consult-disposition-pill active' : 'consult-disposition-pill'}
                        disabled={readOnly && !amending}
                        onClick={() =>
                          setConsultationForm({
                            ...consultationForm,
                            disposition: consultationForm.disposition === d ? '' : d,
                          })
                        }
                      >
                        {DISPOSITION_LABELS[d]}
                      </button>
                    ))}
                  </div>
                </label>
                {consultationForm.disposition && (
                  <label className="inline-label">
                    Disposition notes
                    <input
                      type="text"
                      placeholder={
                        consultationForm.disposition === 'REFER'
                          ? 'e.g. Refer to cardiology'
                          : consultationForm.disposition === 'EMERGENCY'
                            ? 'e.g. Reason for emergency escalation'
                            : undefined
                      }
                      value={consultationForm.disposition_notes}
                      disabled={readOnly && !amending}
                      onChange={(e) => setConsultationForm({ ...consultationForm, disposition_notes: e.target.value })}
                    />
                  </label>
                )}

                {amending && (
                  <>
                    <label className="inline-label">
                      Reason for amendment *
                      <textarea
                        rows={2}
                        value={amendReason}
                        onChange={(e) => setAmendReason(e.target.value)}
                        placeholder="Why is this consultation being corrected?"
                      />
                    </label>
                    {amendError && <p className="error">{amendError}</p>}
                    <div className="doctor-quick-actions">
                      <button
                        type="button"
                        className="btn"
                        disabled={
                          amendSaving ||
                          !amendReason.trim() ||
                          !consultationForm.chief_complaint.trim() ||
                          !consultationForm.diagnosis.trim()
                        }
                        onClick={handleSaveAmendment}
                      >
                        {amendSaving ? 'Saving…' : 'Save amendment'}
                      </button>
                      <button
                        type="button"
                        className="btn-secondary btn"
                        disabled={amendSaving}
                        onClick={() => {
                          setAmending(false)
                          if (consultation) setConsultationForm(consultationFormFromRecord(consultation))
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </>
                )}

                {!amending && consultation?.status === 'COMPLETED' && canAmendConsultation && (
                  <div className="doctor-quick-actions">
                    <button type="button" className="btn-secondary btn btn-sm" onClick={startAmend}>
                      Amend consultation
                    </button>
                  </div>
                )}

                {!amending && amendments.length > 0 && (
                  <div className="amendment-history">
                    <button type="button" className="link" onClick={() => setShowAmendHistory(!showAmendHistory)}>
                      {showAmendHistory ? 'Hide' : 'Show'} amendment history ({amendments.length})
                    </button>
                    {showAmendHistory && (
                      <ul className="amendment-history-list">
                        {amendments.map((a) => (
                          <li key={a.id}>
                            <p className="muted">
                              {formatDateTime(a.amended_at)} · {a.amended_by_username}
                            </p>
                            <p>{a.reason}</p>
                            {a.previous_diagnosis && <p className="muted">Previous diagnosis: {a.previous_diagnosis}</p>}
                            {a.previous_disposition && a.previous_disposition !== 'ADMIT_TO_IPD' && (
                              <p className="muted">
                                Previous disposition: {DISPOSITION_LABELS[a.previous_disposition]}
                              </p>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}

                {consultationSavedAt !== null && !consultationSaving && (
                  <span className="muted">
                    <CheckCircle size={14} weight="fill" /> Draft saved
                  </span>
                )}

                <div className="consult-lock-note">
                  <Warning size={16} aria-hidden="true" />
                  <div>
                    <b>Complete visit</b> locks this note immediately. Later corrections go through an amendment
                    with a reason, kept in the audit trail. Save draft as often as you like before completing.
                  </div>
                </div>
              </section>
            </div>
          )}

          {tab === 'triage' && (
            <div className="detail-section">
              {latestVitals && <p className="muted">Last recorded {formatDateTime(latestVitals.recorded_at)}</p>}
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

              {!readOnly && canRecordVitals && (
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

          {tab === 'orders' && (
            <div className="detail-section">
              {orderError && <p className="error">{orderError}</p>}

              {!readOnly && canCreateOrders && !showNewOrderForm && (
                <button type="button" className="btn-dashed" onClick={() => setShowNewOrderForm(true)}>
                  + New order
                </button>
              )}

              {!readOnly && canCreateOrders && showNewOrderForm && (
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
                    <select value={orderPriority} onChange={(e) => setOrderPriority(e.target.value as OrderPriority)}>
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
                    <input type="text" value={orderIndication} onChange={(e) => setOrderIndication(e.target.value)} />
                  </label>
                  <div className="doctor-form-full doctor-quick-actions">
                    <button
                      type="button"
                      className="btn"
                      disabled={
                        orderSaving || !orderDescription.trim() || (orderType === 'EXTERNAL_REFERRAL' && !orderDestination.trim())
                      }
                      onClick={handleCreateOrder}
                    >
                      {orderSaving ? 'Adding…' : 'Add order'}
                    </button>
                    <button type="button" className="btn-secondary btn" onClick={() => setShowNewOrderForm(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {orders.length === 0 && <p className="muted">No orders yet for this visit.</p>}

              {orders.map((order) => {
                // Cancel stays available through every "still open" LAB/
                // RADIOLOGY status (migrations/0054), matching cancel_
                // order_service's own guard (blocked only once COMPLETED/
                // CANCELLED). Record result stays reachable pre-
                // verification (ORDERED/COLLECTED/IN_PROGRESS) -- verify/
                // release are the Lab/Radiology Worklist's job, never a
                // button in this tab (migrations/0054's own comment on
                // that separation), which is why this tab only ever
                // shows a status and a link there, never a Verify/
                // Release action of its own.
                const cancellable = order.status !== 'COMPLETED' && order.status !== 'CANCELLED'
                const resultable = order.status === 'ORDERED' || order.status === 'COLLECTED' || order.status === 'IN_PROGRESS'
                const actionable = cancellable || resultable
                const isLabRadiology = order.order_type === 'LAB' || order.order_type === 'RADIOLOGY'
                const stageIndex = LAB_RADIOLOGY_STAGES.findIndex((s) => s.status === order.status)

                return (
                  <div key={order.id} className="consult-order-card">
                    <div className="consult-order-card-head">
                      <div>
                        <strong>{order.description}</strong>
                        <div className="muted">
                          {ORDER_TYPE_LABELS[order.order_type]}
                          {order.order_type === 'EXTERNAL_REFERRAL' && order.external_destination
                            ? ` · to ${order.external_destination}`
                            : ''}
                        </div>
                      </div>
                      {!isLabRadiology && <span className={`pill status-${order.status.toLowerCase()}`}>{order.status}</span>}
                    </div>

                    {order.status === 'CANCELLED' && order.cancel_reason && (
                      <p className="muted">Cancelled: {order.cancel_reason}</p>
                    )}

                    {isLabRadiology && order.status !== 'CANCELLED' && (
                      <>
                        <div className="consult-order-stage-bar">
                          {LAB_RADIOLOGY_STAGES.map((stage, i) => (
                            <div
                              key={stage.status}
                              className={i <= stageIndex ? 'consult-order-stage-seg filled' : 'consult-order-stage-seg'}
                            />
                          ))}
                        </div>
                        <div className="consult-order-stage-labels">
                          {LAB_RADIOLOGY_STAGES.map((stage, i) => (
                            <span key={stage.status} className={i === stageIndex ? 'active' : undefined}>
                              {stage.label}
                            </span>
                          ))}
                        </div>
                      </>
                    )}

                    {order.results.length > 0 && (
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
                                {!r.is_critical && r.is_abnormal && <span className="pill status-pending">Abnormal</span>}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}

                    {isLabRadiology && (
                      <div className="muted consult-order-worklist-note">
                        <Warning size={13} aria-hidden="true" /> Verification and release are done by the lab, not
                        from this chart.
                      </div>
                    )}

                    <div className="doctor-quick-actions">
                      {isLabRadiology && (
                        <button type="button" className="btn-secondary btn btn-sm" onClick={onOpenLabWorklist}>
                          Open worklist
                        </button>
                      )}
                      <button type="button" className="btn-secondary btn btn-sm" onClick={() => setPrintOrderTarget(order)}>
                        Print requisition
                      </button>
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
                          <>
                            {resultable && (
                              <button type="button" className="btn btn-sm" onClick={() => startRecordResult(order.id)}>
                                Record result
                              </button>
                            )}
                            {cancellable && (
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
                            )}
                          </>
                        ))}
                    </div>

                    {resultTargetId === order.id && (
                      <div className="consult-result-form">
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
                              <input type="text" value={item.unit ?? ''} onChange={(e) => updateResultItem(index, { unit: e.target.value })} />
                            </label>
                            <label className="inline-label">
                              Reference range
                              <input
                                type="text"
                                value={item.reference_range ?? ''}
                                onChange={(e) => updateResultItem(index, { reference_range: e.target.value })}
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
                            disabled={resultSaving || !resultItems.some((i) => i.parameter.trim() && i.result_value.trim())}
                            onClick={() => handleSaveResult(order.id)}
                          >
                            {resultSaving ? 'Saving…' : 'Save result'}
                          </button>
                          <button type="button" className="btn-secondary btn btn-sm" onClick={cancelRecordResult}>
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}

              {/* master spec section 54's gap #6: lab/radiology orders
                  had no requisition print view. Only ever holds ONE
                  order at a time (printOrderTarget) -- a per-row Print
                  requisition button setting shared state, not a
                  print-area rendered once per row, which would put
                  every LAB/RADIOLOGY order on the page at once. */}
              {printOrderTarget && encounter && (
                <div className="print-area print-only requisition-print-area">
                  <h3>{ORDER_TYPE_LABELS[printOrderTarget.order_type]} Requisition</h3>
                  <p>
                    {encounter.patient_name} ({encounter.patient_uhid})
                  </p>
                  <p>{encounter.doctor_name}</p>
                  <p className="muted">{formatDateTime(printOrderTarget.ordered_at)}</p>
                  <p>
                    <strong>Test/procedure:</strong> {printOrderTarget.description}
                  </p>
                  {printOrderTarget.clinical_indication && (
                    <p>
                      <strong>Clinical indication:</strong> {printOrderTarget.clinical_indication}
                    </p>
                  )}
                  <p>
                    <strong>Priority:</strong> {printOrderTarget.priority}
                  </p>
                </div>
              )}
            </div>
          )}

          {tab === 'prescription' && encounter && (
            <PrescriptionPanel
              appointmentId={appointmentId}
              appointmentCheckedIn={encounter.appointment_status === 'CHECKED_IN'}
              canCreatePrescriptions={canCreatePrescriptions}
              patientName={encounter.patient_name}
              patientUhid={encounter.patient_uhid}
              doctorName={encounter.doctor_name}
            />
          )}

          {tab === 'billing' && encounter && (
            <AppointmentBillingPanel
              appointmentId={appointmentId}
              canManageBilling={canManageBilling}
              patientName={encounter.patient_name}
              patientUhid={encounter.patient_uhid}
              doctorName={encounter.doctor_name}
            />
          )}
        </>
      )}
    </section>
  )
}
