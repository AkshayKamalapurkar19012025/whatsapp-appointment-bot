import { useEffect, useState } from 'react'
import { Warning } from '@phosphor-icons/react'
import {
  ApiError,
  addPrescriptionItem,
  cancelPrescription,
  getPrescription,
  prescribePrescription,
  removePrescriptionItem,
} from '../api'
import type { AllergyConflict, Prescription, PrescriptionItemInput } from '../types'
import { formatDateTime } from '../format'
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

const BLANK_ITEM: PrescriptionItemInput = {
  medicine_name: '',
  generic_name: '',
  dosage: '',
  route: '',
  frequency: '',
  duration: '',
  quantity: 1,
  food_instructions: '',
  special_instructions: '',
}

// OPD/HIMS master spec Phase 8 -- the Prescription tab of Consultation
// Workspace.tsx. Self-contained and lazily loaded (fetches its own
// data when this tab is first opened) rather than threaded through the
// parent's combined initial load, unlike the Triage/Consultation/
// Orders tabs -- this component was added once that file was already
// over a thousand lines, and keeping the addition self-contained beat
// growing that file's already-tangled shared loading sequence further.
export default function PrescriptionPanel({
  appointmentId,
  appointmentCheckedIn,
  canCreatePrescriptions,
  patientName,
  patientUhid,
  doctorName,
}: {
  appointmentId: number
  appointmentCheckedIn: boolean
  // prescription.create (migrations/0048_clinical_rbac_permissions.sql,
  // DOCTOR/STAFF/ADMIN) -- a role without it (RECEPTIONIST/NURSE/
  // LAB_TECH/PHARMACIST/BILLING) can still view an existing
  // prescription read-only, same as any other GET, but the add-item/
  // prescribe actions below would 403 server-side, so they're hidden
  // rather than left to fail.
  canCreatePrescriptions: boolean
  patientName: string
  patientUhid: string
  doctorName: string
}) {
  const [prescription, setPrescription] = useState<Prescription | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notCheckedIn, setNotCheckedIn] = useState(false)

  const [form, setForm] = useState<PrescriptionItemInput>(BLANK_ITEM)
  const [adding, setAdding] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [removingId, setRemovingId] = useState<number | null>(null)
  const [prescribing, setPrescribing] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [showCancelForm, setShowCancelForm] = useState(false)
  const [cancelReason, setCancelReason] = useState('')
  // P0 clinical safety -- set when the backend finds a text match
  // between this medicine and one of the patient's recorded allergies
  // (app/services/allergy_check_service.py). The pending item's own
  // payload is kept in `form` (not cleared) so the dialog's Continue/
  // Cancel actions can resubmit the exact same add-item call with the
  // clinician's decision -- see handleAllergyDecision.
  const [allergyConflicts, setAllergyConflicts] = useState<AllergyConflict[] | null>(null)
  const [decidingAllergy, setDecidingAllergy] = useState(false)

  useEffect(() => {
    let cancelledEffect = false
    setLoading(true)
    setLoadError(null)
    setNotCheckedIn(false)

    getPrescription(appointmentId)
      .then((p) => {
        if (!cancelledEffect) setPrescription(p)
      })
      .catch((err) => {
        if (cancelledEffect) return
        if (err instanceof ApiError && err.status === 409) {
          setNotCheckedIn(true)
        } else {
          setLoadError(err instanceof ApiError ? err.message : 'Could not load the prescription')
        }
      })
      .finally(() => {
        if (!cancelledEffect) setLoading(false)
      })

    return () => {
      cancelledEffect = true
    }
  }, [appointmentId])

  function trimmedFormPayload(): PrescriptionItemInput {
    return {
      ...form,
      generic_name: form.generic_name?.trim() || undefined,
      dosage: form.dosage?.trim() || undefined,
      route: form.route?.trim() || undefined,
      frequency: form.frequency?.trim() || undefined,
      duration: form.duration?.trim() || undefined,
      food_instructions: form.food_instructions?.trim() || undefined,
      special_instructions: form.special_instructions?.trim() || undefined,
    }
  }

  async function handleAddItem() {
    setAdding(true)
    setActionError(null)
    try {
      const result = await addPrescriptionItem(appointmentId, trimmedFormPayload())
      setPrescription(result.prescription)
      if (result.allergy_warning) {
        // Not added yet -- keep `form` as-is so the dialog can resubmit
        // it with the clinician's decision.
        setAllergyConflicts(result.allergy_warning.conflicts)
      } else {
        setForm(BLANK_ITEM)
      }
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not add this medicine')
    } finally {
      setAdding(false)
    }
  }

  async function handleAllergyDecision(decision: 'continue' | 'cancel') {
    setDecidingAllergy(true)
    setActionError(null)
    try {
      const result = await addPrescriptionItem(appointmentId, trimmedFormPayload(), decision)
      setPrescription(result.prescription)
      setAllergyConflicts(null)
      setForm(BLANK_ITEM)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not record this decision')
    } finally {
      setDecidingAllergy(false)
    }
  }

  async function handleRemoveItem(itemId: number) {
    setRemovingId(itemId)
    setActionError(null)
    try {
      setPrescription(await removePrescriptionItem(appointmentId, itemId))
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not remove this medicine')
    } finally {
      setRemovingId(null)
    }
  }

  async function handlePrescribe() {
    setPrescribing(true)
    setActionError(null)
    try {
      setPrescription(await prescribePrescription(appointmentId))
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not send this prescription to pharmacy')
    } finally {
      setPrescribing(false)
    }
  }

  async function handleCancel() {
    if (!cancelReason.trim()) return
    setCancelling(true)
    setActionError(null)
    try {
      setPrescription(await cancelPrescription(appointmentId, cancelReason.trim()))
      setShowCancelForm(false)
      setCancelReason('')
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not cancel this prescription')
    } finally {
      setCancelling(false)
    }
  }

  if (loading) {
    return (
      <div className="state-block">
        <span className="spinner" aria-hidden="true" />
        Loading…
      </div>
    )
  }

  if (loadError) {
    return <p className="error">{loadError}</p>
  }

  if (notCheckedIn || !prescription) {
    return (
      <div className="state-block">
        <Warning size={20} weight="regular" />
        This patient isn&apos;t currently checked in. A prescription can only be started while the visit is in
        progress.
      </div>
    )
  }

  const editable = prescription.status === 'DRAFT' && appointmentCheckedIn && canCreatePrescriptions
  const canPrescribe = editable && prescription.items.length > 0
  const canCancel = prescription.status === 'PRESCRIBED' && !prescription.items.some((i) => i.quantity_dispensed > 0)

  return (
    <div className="detail-section">
      {prescription.status !== 'DRAFT' && (
        <p className="muted">
          {prescription.status === 'PRESCRIBED' &&
            `Sent to pharmacy ${prescription.prescribed_at ? formatDateTime(prescription.prescribed_at) : ''}`}
          {prescription.status === 'CANCELLED' && `Cancelled: ${prescription.cancel_reason ?? ''}`}
        </p>
      )}
      {actionError && <p className="error">{actionError}</p>}

      {editable && (
        <div className="doctor-form-grid">
          <label className="inline-label doctor-form-full">
            Medicine *
            <input
              type="text"
              value={form.medicine_name}
              onChange={(e) => setForm({ ...form, medicine_name: e.target.value })}
              placeholder="e.g. Paracetamol"
            />
          </label>
          <label className="inline-label">
            Generic name
            <input
              type="text"
              value={form.generic_name}
              onChange={(e) => setForm({ ...form, generic_name: e.target.value })}
            />
          </label>
          <label className="inline-label">
            Dosage
            <input
              type="text"
              value={form.dosage}
              onChange={(e) => setForm({ ...form, dosage: e.target.value })}
              placeholder="e.g. 500mg"
            />
          </label>
          <label className="inline-label">
            Route
            <input
              type="text"
              value={form.route}
              onChange={(e) => setForm({ ...form, route: e.target.value })}
              placeholder="e.g. Oral"
            />
          </label>
          <label className="inline-label">
            Frequency
            <input
              type="text"
              value={form.frequency}
              onChange={(e) => setForm({ ...form, frequency: e.target.value })}
              placeholder="e.g. 1-0-1"
            />
          </label>
          <label className="inline-label">
            Duration
            <input
              type="text"
              value={form.duration}
              onChange={(e) => setForm({ ...form, duration: e.target.value })}
              placeholder="e.g. 5 days"
            />
          </label>
          <label className="inline-label">
            Quantity *
            <input
              type="number"
              min={1}
              value={form.quantity}
              onChange={(e) => setForm({ ...form, quantity: Number(e.target.value) })}
            />
          </label>
          <label className="inline-label doctor-form-full">
            Food instructions
            <input
              type="text"
              value={form.food_instructions}
              onChange={(e) => setForm({ ...form, food_instructions: e.target.value })}
              placeholder="e.g. After food"
            />
          </label>
          <label className="inline-label doctor-form-full">
            Special instructions
            <input
              type="text"
              value={form.special_instructions}
              onChange={(e) => setForm({ ...form, special_instructions: e.target.value })}
            />
          </label>
          <div className="doctor-form-full">
            <button
              type="button"
              className="btn-secondary btn"
              disabled={adding || !form.medicine_name.trim() || !form.quantity}
              onClick={handleAddItem}
            >
              {adding ? 'Adding…' : '+ Add medicine'}
            </button>
          </div>
        </div>
      )}

      {prescription.items.length === 0 && <p className="muted">No medicines added yet.</p>}

      {prescription.items.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Medicine</th>
              <th>Dosage</th>
              <th>Frequency</th>
              <th>Duration</th>
              <th>Qty</th>
              <th>Status</th>
              {editable && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {prescription.items.map((item) => (
              <tr key={item.id}>
                <td>
                  {item.medicine_name}
                  {item.generic_name && <div className="muted">{item.generic_name}</div>}
                  {(item.food_instructions || item.special_instructions) && (
                    <div className="muted">
                      {[item.food_instructions, item.special_instructions].filter(Boolean).join(' · ')}
                    </div>
                  )}
                </td>
                <td>{item.dosage || '—'}</td>
                <td>{item.frequency || '—'}</td>
                <td>{item.duration || '—'}</td>
                <td>
                  {item.quantity_dispensed}/{item.quantity}
                </td>
                <td>
                  <span className={`pill status-${item.dispense_status.toLowerCase()}`}>
                    {item.dispense_status.replace('_', ' ')}
                  </span>
                </td>
                {editable && (
                  <td>
                    <button
                      type="button"
                      className="btn-danger btn btn-sm"
                      disabled={removingId === item.id}
                      onClick={() => handleRemoveItem(item.id)}
                    >
                      {removingId === item.id ? 'Removing…' : 'Remove'}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {(canPrescribe || canCancel || prescription.items.length > 0) && (
        <div className="doctor-quick-actions">
          {canPrescribe && (
            <button type="button" className="btn" disabled={prescribing} onClick={handlePrescribe}>
              {prescribing ? 'Sending…' : 'Send to pharmacy'}
            </button>
          )}
          {prescription.items.length > 0 && (
            <button type="button" className="btn-secondary btn btn-sm" onClick={() => window.print()}>
              Print prescription
            </button>
          )}
          {canCancel &&
            (showCancelForm ? (
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
                  onClick={handleCancel}
                >
                  {cancelling ? 'Cancelling…' : 'Confirm'}
                </button>
                <button type="button" className="btn-secondary btn btn-sm" onClick={() => setShowCancelForm(false)}>
                  Back
                </button>
              </div>
            ) : (
              <button type="button" className="btn-danger btn btn-sm" onClick={() => setShowCancelForm(true)}>
                Cancel prescription
              </button>
            ))}
        </div>
      )}

      {/* master spec section 54's gap #6: prescription had no print
          view at all. print-area + print-only (styles.css) give it
          its own layout, separate from the on-screen editable table
          above (which includes Remove buttons/dispense status that
          don't belong on a prescription slip). */}
      {prescription.items.length > 0 && (
        <div className="print-area print-only prescription-print-area">
          <h3>Prescription</h3>
          <p>
            {patientName} ({patientUhid})
          </p>
          <p>{doctorName}</p>
          <p className="muted">{formatDateTime(prescription.prescribed_at ?? new Date().toISOString())}</p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Medicine</th>
                <th>Dosage</th>
                <th>Frequency</th>
                <th>Duration</th>
                <th>Qty</th>
              </tr>
            </thead>
            <tbody>
              {prescription.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    {item.medicine_name}
                    {item.generic_name && <div className="muted">{item.generic_name}</div>}
                    {(item.food_instructions || item.special_instructions) && (
                      <div className="muted">
                        {[item.food_instructions, item.special_instructions].filter(Boolean).join(' · ')}
                      </div>
                    )}
                  </td>
                  <td>{item.dosage || '—'}</td>
                  <td>{item.frequency || '—'}</td>
                  <td>{item.duration || '—'}</td>
                  <td>{item.quantity}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* P0 clinical safety -- a text match against one of the patient's
          recorded allergies (app/services/allergy_check_service.py).
          Deliberately non-blocking (master spec Visit Completion
          precedent, same "warn, let the human decide" pattern as
          patient-duplicate detection): the clinician can still add the
          medicine, but never without seeing this and making an explicit
          choice, which is then audited either way. */}
      <AlertDialog open={allergyConflicts !== null} onOpenChange={(open) => !open && setAllergyConflicts(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              <Warning size={18} weight="fill" style={{ color: 'var(--color-danger)', marginRight: '0.4rem' }} />
              Allergy Warning
            </AlertDialogTitle>
            <AlertDialogDescription>
              {patientName} has a recorded allergy that may conflict with{' '}
              <strong>{form.medicine_name}</strong>. This is a same-text match against the recorded allergen, not a
              clinical assessment -- review before continuing.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <ul className="visit-completion-checklist">
            {(allergyConflicts ?? []).map((c) => (
              <li key={c.allergy_id} className="pending">
                <Warning size={16} />
                Allergy: {c.allergen}
                {c.severity && ` (${c.severity})`} — matched against &quot;{c.matched_against}&quot;
                {c.reaction && ` · Reaction: ${c.reaction}`}
              </li>
            ))}
          </ul>

          <AlertDialogFooter>
            <AlertDialogCancel
              disabled={decidingAllergy}
              onClick={() => handleAllergyDecision('cancel')}
            >
              {decidingAllergy ? 'Please wait…' : 'Cancel Prescription'}
            </AlertDialogCancel>
            <AlertDialogAction variant="danger" disabled={decidingAllergy} onClick={() => handleAllergyDecision('continue')}>
              {decidingAllergy ? 'Please wait…' : 'Continue Anyway'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
